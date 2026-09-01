#include "es_shim.h"

#include <EndpointSecurity/EndpointSecurity.h>
#include <arpa/inet.h>
#include <bsm/libbsm.h>
#include <libproc.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc_info.h>

struct essh_client {
    es_client_t *inner;
};

/* es_string_token_t 不保证 NUL 结尾：按 data+length 截断拷贝并补 NUL。 */
static void copy_token(char *dst, size_t cap, uint32_t *len_out, es_string_token_t tok) {
    size_t n = tok.length;
    if (n >= cap) n = cap - 1;
    if (n > 0 && tok.data) memcpy(dst, tok.data, n);
    dst[n] = '\0';
    *len_out = (uint32_t)n;
}

/* 把 dir/file 形式的目标拼接成完整路径（CREATE new_path / RENAME new_path /
 * LINK / CLONE / UIPC_BIND 共用）。 */
static void join_path(char *dst, size_t cap, es_string_token_t filename) {
    size_t cur = strlen(dst);
    if (cur == 0 || cur >= cap - 2) return;
    if (dst[cur - 1] != '/') dst[cur++] = '/';
    size_t n = filename.length;
    if (n > cap - 1 - cur) n = cap - 1 - cur;
    if (n > 0 && filename.data) memcpy(dst + cur, filename.data, n);
    dst[cur + n] = '\0';
}

static void fill_file(essh_file_t *dst, const es_file_t *f) {
    memset(dst, 0, sizeof(*dst));
    copy_token(dst->path, sizeof(dst->path), &dst->path_len, f->path);
    dst->path_truncated = f->path_truncated ? 1 : 0;
    dst->ino = (uint64_t)f->stat.st_ino;
    dst->size = (int64_t)f->stat.st_size;
    dst->mtime_sec = (int64_t)f->stat.st_mtimespec.tv_sec;
    dst->mode = (uint32_t)f->stat.st_mode;
    dst->uid = (uint32_t)f->stat.st_uid;
    dst->gid = (uint32_t)f->stat.st_gid;
}

static void fill_process(essh_process_t *dst, const es_process_t *p) {
    audit_token_t t = p->audit_token;
    memset(dst, 0, sizeof(*dst));
    dst->pid = (int32_t)audit_token_to_pid(t);
    dst->pidversion = (uint32_t)audit_token_to_pidversion(t);
    dst->ppid = p->ppid;
    dst->original_ppid = p->original_ppid;
    dst->responsible_pid = (int32_t)audit_token_to_pid(p->responsible_audit_token);
    dst->ruid = audit_token_to_ruid(t);
    dst->euid = audit_token_to_euid(t);
    dst->rgid = audit_token_to_rgid(t);
    dst->egid = audit_token_to_egid(t);
    dst->auid = audit_token_to_auid(t);
    dst->codesigning_flags = p->codesigning_flags;
    dst->is_platform_binary = p->is_platform_binary ? 1 : 0;
    dst->is_es_client = p->is_es_client ? 1 : 0;
    dst->start_time_sec = (int64_t)p->start_time.tv_sec;
    memcpy(dst->cdhash, p->cdhash, sizeof(dst->cdhash));
    copy_token(dst->executable, sizeof(dst->executable), &dst->executable_len,
               p->executable->path);
    copy_token(dst->signing_id, sizeof(dst->signing_id), &dst->signing_id_len,
               p->signing_id);
    copy_token(dst->team_id, sizeof(dst->team_id), &dst->team_id_len, p->team_id);
}

static void fill_exec(essh_message_t *out, const es_message_t *msg) {
    const es_event_exec_t *ev = &msg->event.exec;
    essh_ev_exec_t *d = &out->event.exec;
    fill_process(&d->target, ev->target);

    uint32_t argc = es_exec_arg_count(ev);
    size_t pos = 0;
    uint32_t n = 0;
    for (uint32_t i = 0; i < argc && n < ESSH_ARGV_MAX; i++) {
        es_string_token_t a = es_exec_arg(ev, i);
        if (a.length > sizeof(d->argv_buf) - pos) break;
        d->argv_offsets[n] = (uint32_t)pos;
        d->argv_lens[n] = (uint16_t)a.length;
        if (a.length > 0 && a.data) memcpy(d->argv_buf + pos, a.data, a.length);
        pos += a.length;
        n++;
    }
    d->argc = n;

    if (msg->version >= 2 && ev->script) {
        fill_file(&d->script, ev->script);
        d->has_script = 1;
    }
    if (msg->version >= 3 && ev->cwd) {
        copy_token(d->cwd, sizeof(d->cwd), &d->cwd_len, ev->cwd->path);
    }
}

/* CREATE / RENAME 的 destination 二态：已存在文件 or 新建路径（dir+filename）。 */
static void fill_destination(essh_file_t *dst_file, uint8_t *existed, uint32_t *mode_out,
                             es_destination_type_t dtype, const es_file_t *existing,
                             const es_file_t *dir, es_string_token_t filename,
                             mode_t new_mode) {
    if (dtype == ES_DESTINATION_TYPE_EXISTING_FILE) {
        fill_file(dst_file, existing);
        *existed = 1;
    } else {
        fill_file(dst_file, dir);
        join_path(dst_file->path, sizeof(dst_file->path), filename);
        dst_file->path_len = (uint32_t)strlen(dst_file->path);
        /* 新路径尚无 stat 意义，仅保留内核给出的 mode */
        dst_file->ino = 0;
        dst_file->size = 0;
        dst_file->mode = (uint32_t)new_mode;
        *existed = 0;
    }
    if (mode_out) *mode_out = (uint32_t)new_mode;
}

static void fill_message(essh_message_t *out, const es_message_t *msg) {
    memset(out, 0, sizeof(*out));
    out->mach_time = msg->mach_time;
    out->thread_id = msg->thread ? msg->thread->thread_id : 0;
    out->time_sec = (int64_t)msg->time.tv_sec;
    out->time_nsec = (int64_t)msg->time.tv_nsec;
    out->seq_num = msg->seq_num;
    out->global_seq_num = msg->global_seq_num;
    out->event_type = (uint32_t)msg->event_type;
    out->message_version = msg->version;
    fill_process(&out->process, msg->process);

    switch (msg->event_type) {
    case ES_EVENT_TYPE_NOTIFY_EXEC:
        fill_exec(out, msg);
        break;
    case ES_EVENT_TYPE_NOTIFY_FORK:
        fill_process(&out->event.fork.child, msg->event.fork.child);
        break;
    case ES_EVENT_TYPE_NOTIFY_EXIT:
        out->event.exit.stat = msg->event.exit.stat;
        break;
    case ES_EVENT_TYPE_NOTIFY_CREATE: {
        const es_event_create_t *ev = &msg->event.create;
        essh_ev_create_t *d = &out->event.create;
        fill_destination(&d->file, &d->created_new, &d->mode, ev->destination_type,
                         ev->destination_type == ES_DESTINATION_TYPE_EXISTING_FILE
                             ? ev->destination.existing_file
                             : NULL,
                         ev->destination_type == ES_DESTINATION_TYPE_NEW_PATH
                             ? ev->destination.new_path.dir
                             : NULL,
                         ev->destination_type == ES_DESTINATION_TYPE_NEW_PATH
                             ? ev->destination.new_path.filename
                             : (es_string_token_t){0},
                         ev->destination_type == ES_DESTINATION_TYPE_NEW_PATH
                             ? ev->destination.new_path.mode
                             : 0);
        break;
    }
    case ES_EVENT_TYPE_NOTIFY_OPEN:
        fill_file(&out->event.open.file, msg->event.open.file);
        out->event.open.fflag = msg->event.open.fflag;
        break;
    case ES_EVENT_TYPE_NOTIFY_CLOSE:
        fill_file(&out->event.close.file, msg->event.close.target);
        out->event.close.modified = msg->event.close.modified ? 1 : 0;
        if (msg->version >= 6)
            out->event.close.was_mapped_writable =
                msg->event.close.was_mapped_writable ? 1 : 0;
        break;
    case ES_EVENT_TYPE_NOTIFY_RENAME: {
        const es_event_rename_t *ev = &msg->event.rename;
        essh_ev_rename_t *d = &out->event.rename;
        fill_file(&d->src, ev->source);
        fill_destination(&d->dst, &d->dst_existed, NULL, ev->destination_type,
                         ev->destination_type == ES_DESTINATION_TYPE_EXISTING_FILE
                             ? ev->destination.existing_file
                             : NULL,
                         ev->destination_type == ES_DESTINATION_TYPE_NEW_PATH
                             ? ev->destination.new_path.dir
                             : NULL,
                         ev->destination_type == ES_DESTINATION_TYPE_NEW_PATH
                             ? ev->destination.new_path.filename
                             : (es_string_token_t){0},
                         0);
        break;
    }
    case ES_EVENT_TYPE_NOTIFY_UNLINK:
        fill_file(&out->event.unlink.target, msg->event.unlink.target);
        fill_file(&out->event.unlink.parent_dir, msg->event.unlink.parent_dir);
        break;
    case ES_EVENT_TYPE_NOTIFY_LINK: {
        const es_event_link_t *ev = &msg->event.link;
        essh_ev_link_t *d = &out->event.link;
        fill_file(&d->src, ev->source);
        fill_file(&d->dst, ev->target_dir);
        join_path(d->dst.path, sizeof(d->dst.path), ev->target_filename);
        d->dst.path_len = (uint32_t)strlen(d->dst.path);
        d->is_dir = (d->src.mode & S_IFMT) == S_IFDIR ? 1 : 0;
        break;
    }
    case ES_EVENT_TYPE_NOTIFY_CLONE: {
        const es_event_clone_t *ev = &msg->event.clone;
        essh_ev_clone_t *d = &out->event.clone;
        fill_file(&d->src, ev->source);
        fill_file(&d->dst, ev->target_dir);
        join_path(d->dst.path, sizeof(d->dst.path), ev->target_name);
        d->dst.path_len = (uint32_t)strlen(d->dst.path);
        d->is_dir = (d->src.mode & S_IFMT) == S_IFDIR ? 1 : 0;
        break;
    }
    case ES_EVENT_TYPE_NOTIFY_SETATTRLIST: {
        const es_event_setattrlist_t *ev = &msg->event.setattrlist;
        essh_ev_setattrlist_t *d = &out->event.setattrlist;
        fill_file(&d->target, ev->target);
        d->commonattr = ev->attrlist.commonattr;
        d->volattr = ev->attrlist.volattr;
        d->dirattr = ev->attrlist.dirattr;
        d->fileattr = ev->attrlist.fileattr;
        d->forkattr = ev->attrlist.forkattr;
        break;
    }
    case ES_EVENT_TYPE_NOTIFY_TRUNCATE:
        fill_file(&out->event.truncate.target, msg->event.truncate.target);
        break;
    case ES_EVENT_TYPE_NOTIFY_UIPC_BIND: {
        const es_event_uipc_bind_t *ev = &msg->event.uipc_bind;
        essh_ev_uipc_bind_t *d = &out->event.uipc_bind;
        fill_file(&d->sock, ev->dir);
        join_path(d->sock.path, sizeof(d->sock.path), ev->filename);
        d->sock.path_len = (uint32_t)strlen(d->sock.path);
        d->mode = (uint32_t)ev->mode;
        break;
    }
    case ES_EVENT_TYPE_NOTIFY_UIPC_CONNECT: {
        const es_event_uipc_connect_t *ev = &msg->event.uipc_connect;
        essh_ev_uipc_connect_t *d = &out->event.uipc_connect;
        fill_file(&d->file, ev->file);
        d->domain = ev->domain;
        d->type = ev->type;
        d->protocol = ev->protocol;
        break;
    }
    /* --extra-events 显式加订事件 */
    case ES_EVENT_TYPE_NOTIFY_WRITE:
        fill_file(&out->event.write.target, msg->event.write.target);
        break;
    case ES_EVENT_TYPE_NOTIFY_READDIR:
        fill_file(&out->event.readdir.dir, msg->event.readdir.target);
        break;
    case ES_EVENT_TYPE_NOTIFY_SIGNAL:
        out->event.signal.sig = msg->event.signal.sig;
        fill_process(&out->event.signal.target, msg->event.signal.target);
        break;
    default:
        break;
    }
}

int essh_client_new(essh_client_t **out, essh_cb cb, void *ctx) {
    es_handler_block_t handler = ^(es_client_t *client, const es_message_t *msg) {
        (void)client; /* NOTIFY-only 无应答，client 无需使用 */
        essh_message_t flat;
        fill_message(&flat, msg);
        cb(ctx, &flat);
    };

    es_client_t *client = NULL;
    es_new_client_result_t rc = es_new_client(&client, handler);
    if (rc != ES_NEW_CLIENT_RESULT_SUCCESS) return (int)rc;

    essh_client_t *wrapper = calloc(1, sizeof(*wrapper));
    if (!wrapper) {
        es_delete_client(client);
        return (int)ES_NEW_CLIENT_RESULT_ERR_INTERNAL;
    }
    wrapper->inner = client;
    *out = wrapper;
    return (int)ES_NEW_CLIENT_RESULT_SUCCESS;
}

int essh_subscribe(essh_client_t *c, uint32_t event_mask, uint32_t extra_mask) {
    /* 位序与 es_shim.h 头部注释一致；数值直接用枚举名，不在此硬编码。 */
    static const es_event_type_t defaults[] = {
        ES_EVENT_TYPE_NOTIFY_EXEC,       ES_EVENT_TYPE_NOTIFY_FORK,
        ES_EVENT_TYPE_NOTIFY_EXIT,       ES_EVENT_TYPE_NOTIFY_CREATE,
        ES_EVENT_TYPE_NOTIFY_OPEN,       ES_EVENT_TYPE_NOTIFY_CLOSE,
        ES_EVENT_TYPE_NOTIFY_RENAME,     ES_EVENT_TYPE_NOTIFY_UNLINK,
        ES_EVENT_TYPE_NOTIFY_LINK,       ES_EVENT_TYPE_NOTIFY_CLONE,
        ES_EVENT_TYPE_NOTIFY_SETATTRLIST, ES_EVENT_TYPE_NOTIFY_TRUNCATE,
        ES_EVENT_TYPE_NOTIFY_UIPC_BIND,  ES_EVENT_TYPE_NOTIFY_UIPC_CONNECT,
    };
    static const es_event_type_t extras[] = {
        ES_EVENT_TYPE_NOTIFY_WRITE,
        ES_EVENT_TYPE_NOTIFY_READDIR,
        ES_EVENT_TYPE_NOTIFY_SIGNAL,
    };
    es_event_type_t events[sizeof(defaults) / sizeof(defaults[0]) +
                           sizeof(extras) / sizeof(extras[0])];
    uint32_t n = 0;
    for (uint32_t i = 0; i < sizeof(defaults) / sizeof(defaults[0]); i++)
        if (event_mask & (1u << i)) events[n++] = defaults[i];
    for (uint32_t i = 0; i < sizeof(extras) / sizeof(extras[0]); i++)
        if (extra_mask & (1u << i)) events[n++] = extras[i];
    if (n == 0) return (int)ES_RETURN_ERROR;
    return (int)es_subscribe(c->inner, events, n);
}

int essh_mute_path_prefix(essh_client_t *c, const char *path) {
    return (int)es_mute_path(c->inner, path, ES_MUTE_PATH_TYPE_TARGET_PREFIX);
}

void essh_client_delete(essh_client_t *c) {
    if (!c) return;
    if (c->inner) es_delete_client(c->inner);
    free(c);
}

int essh_list_sockets(int32_t pid, essh_socket_t **out, size_t *count) {
    *out = NULL;
    *count = 0;

    int needed = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, NULL, 0);
    if (needed <= 0) return -1;
    struct proc_fdinfo *fds = malloc((size_t)needed);
    if (!fds) return -1;
    int got = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, fds, needed);
    if (got <= 0) {
        free(fds);
        return -1;
    }
    size_t nfd = (size_t)got / sizeof(struct proc_fdinfo);

    essh_socket_t *list = calloc(nfd, sizeof(essh_socket_t));
    if (!list) {
        free(fds);
        return -1;
    }
    size_t n = 0;
    for (size_t i = 0; i < nfd; i++) {
        if (fds[i].proc_fdtype != PROX_FDTYPE_SOCKET) continue;
        struct socket_fdinfo sfi;
        memset(&sfi, 0, sizeof(sfi));
        /* fd 级 flavor 必须走 proc_pidfdinfo(pid, fd, flavor, ...)——误用
         * proc_pidinfo(pid, flavor=3, ...) 会命中 PROC_PIDT_BSDINFO(同为 3)，
         * 返回 136 字节的 proc_bsdinfo 而非 socket_fdinfo。 */
        if (proc_pidfdinfo(pid, fds[i].proc_fd, PROC_PIDFDSOCKETINFO, &sfi,
                           (int)sizeof(sfi)) != (int)sizeof(sfi))
            continue;
        struct socket_info *si = &sfi.psi;
        if (si->soi_family != AF_INET && si->soi_family != AF_INET6) continue;

        const struct in_sockinfo *in = NULL;
        int32_t state = -1;
        if (si->soi_kind == SOCKINFO_TCP) {
            in = &si->soi_proto.pri_tcp.tcpsi_ini;
            state = si->soi_proto.pri_tcp.tcpsi_state;
        } else if (si->soi_kind == SOCKINFO_IN) {
            in = &si->soi_proto.pri_in;
        } else {
            continue;
        }

        essh_socket_t *s = &list[n];
        s->family = si->soi_family;
        s->type = si->soi_type;
        s->protocol = (int32_t)si->soi_protocol;
        s->state = state;
        s->lport = (uint32_t)ntohs((uint16_t)in->insi_lport);
        s->fport = (uint32_t)ntohs((uint16_t)in->insi_fport);

        int af = (in->insi_vflag & INI_IPV6) ? AF_INET6 : AF_INET;
        const void *la = (af == AF_INET6) ? (const void *)&in->insi_laddr.ina_6
                                          : (const void *)&in->insi_laddr.ina_46.i46a_addr4;
        const void *fa = (af == AF_INET6) ? (const void *)&in->insi_faddr.ina_6
                                          : (const void *)&in->insi_faddr.ina_46.i46a_addr4;
        if (!inet_ntop(af, la, s->laddr, sizeof(s->laddr))) s->laddr[0] = '\0';
        if (!inet_ntop(af, fa, s->faddr, sizeof(s->faddr))) s->faddr[0] = '\0';
        s->family = af;
        n++;
    }
    free(fds);

    if (n == 0) {
        free(list);
        return 0;
    }
    *out = list;
    *count = n;
    return 0;
}

void essh_free_sockets(essh_socket_t *s, size_t count) {
    (void)count;
    free(s);
}
