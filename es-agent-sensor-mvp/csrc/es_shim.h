/*
 * es_shim — libEndpointSecurity 的极简 C 垫片。
 *
 * 职责仅限三件事，均无法（或不宜）用纯 Rust 表达：
 *  1. es_message_t 的字段提取（其布局大且随 message version 演进，
 *     手写 repr(C) 镜像整个 es_message_t 风险远高于收益）；
 *  2. es_handler_block_t 的 blocks 语法桥接为普通 C 函数指针；
 *  3. libproc socket 枚举（proc_info.h 结构体复杂，C 侧直接用系统头文件更可靠）。
 *
 * 所有策略逻辑（过滤、归一化、进程树）都在 Rust 侧，本文件不含任何业务判断。
 *
 * 事件订阅掩码约定（essh_subscribe 的 event_mask / extra_mask 位序，
 * Rust 侧以同名常量镜像，改动必须双向同步）：
 *   event_mask bit0..13: EXEC FORK EXIT CREATE OPEN CLOSE RENAME UNLINK
 *                        LINK CLONE SETATTRLIST TRUNCATE UIPC_BIND UIPC_CONNECT
 *   extra_mask bit0..2:  WRITE READDIR SIGNAL
 *
 * 注意：macOS ES 没有独立的 RMDIR 事件，rmdir(2) 在内核中归并为 UNLINK
 * （目标是目录），Rust 侧按 stat mode 区分 file/dir。
 */
#ifndef ES_SHIM_H
#define ES_SHIM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct essh_client essh_client_t; /* 不透明句柄，包裹 es_client_t */

#define ESSH_PATH_CAP 4096
#define ESSH_TOKEN_CAP 256
#define ESSH_ARGV_MAX 64
#define ESSH_ARGV_BUFSIZE 8192
#define ESSH_ADDR_CAP 64

/* es_file_t 的扁平提取。path 保证 NUL 结尾；path_len 为原始长度（可能 > path 实际存下部分）。 */
typedef struct {
    uint64_t ino;
    int64_t size;
    int64_t mtime_sec;
    uint32_t mode;
    uint32_t uid;
    uint32_t gid;
    uint8_t path_truncated;
    uint32_t path_len;
    char path[ESSH_PATH_CAP];
} essh_file_t;

/* es_process_t 的扁平提取。pid/pidversion/cred 经 libbsm audit_token 转换取得。 */
typedef struct {
    int32_t pid;
    uint32_t pidversion;
    int32_t ppid;
    int32_t original_ppid;
    int32_t responsible_pid;
    uint32_t ruid;
    uint32_t euid;
    uint32_t rgid;
    uint32_t egid;
    uint32_t auid;
    uint32_t codesigning_flags;
    uint8_t is_platform_binary;
    uint8_t is_es_client;
    int64_t start_time_sec;
    uint8_t cdhash[20];
    uint32_t executable_len;
    char executable[ESSH_PATH_CAP];
    uint32_t signing_id_len;
    char signing_id[ESSH_TOKEN_CAP];
    uint32_t team_id_len;
    char team_id[ESSH_TOKEN_CAP];
} essh_process_t;

typedef struct {
    essh_process_t target;
    uint32_t argc;
    uint32_t argv_offsets[ESSH_ARGV_MAX];
    uint16_t argv_lens[ESSH_ARGV_MAX];
    uint8_t argv_buf[ESSH_ARGV_BUFSIZE];
    uint8_t has_script;
    essh_file_t script;
    uint32_t cwd_len;
    char cwd[ESSH_PATH_CAP];
} essh_ev_exec_t;

typedef struct {
    essh_process_t child;
} essh_ev_fork_t;

typedef struct {
    int32_t stat;
} essh_ev_exit_t;

typedef struct {
    essh_file_t file;
    uint32_t mode; /* 新建路径的 mode（created_new=1 时有效） */
    uint8_t created_new; /* 1 = 目标此前不存在（ES new_path 分支） */
} essh_ev_create_t;

typedef struct {
    essh_file_t file;
    int32_t fflag;
} essh_ev_open_t;

typedef struct {
    essh_file_t file;
    uint8_t modified;
    uint8_t was_mapped_writable;
} essh_ev_close_t;

typedef struct {
    essh_file_t src;
    essh_file_t dst;
    uint8_t dst_existed; /* 1 = dst 已存在（被覆盖） */
} essh_ev_rename_t;

typedef struct {
    essh_file_t target;
    essh_file_t parent_dir;
} essh_ev_unlink_t;

typedef struct {
    essh_file_t src;
    essh_file_t dst;
    uint8_t is_dir;
} essh_ev_link_t;

typedef struct {
    essh_file_t src;
    essh_file_t dst;
    uint8_t is_dir;
} essh_ev_clone_t;

typedef struct {
    essh_file_t target;
    uint32_t commonattr;
    uint32_t volattr;
    uint32_t dirattr;
    uint32_t fileattr;
    uint32_t forkattr;
} essh_ev_setattrlist_t;

typedef struct {
    essh_file_t target;
} essh_ev_truncate_t;

typedef struct {
    essh_file_t sock; /* dir + filename 拼接后的 socket 路径 */
    uint32_t mode;
} essh_ev_uipc_bind_t;

typedef struct {
    essh_file_t file;
    int32_t domain;
    int32_t type;
    int32_t protocol;
} essh_ev_uipc_connect_t;

/* 以下三个为 --extra-events 显式加订事件的最小字段提取 */
typedef struct {
    essh_file_t target;
} essh_ev_write_t;

typedef struct {
    essh_file_t dir;
} essh_ev_readdir_t;

typedef struct {
    int32_t sig;
    essh_process_t target;
} essh_ev_signal_t;

/* tagged union：判别标签为外层 essh_message_t.event_type（即 es_event_type_t 数值）。 */
typedef union {
    essh_ev_exec_t exec;
    essh_ev_fork_t fork;
    essh_ev_exit_t exit;
    essh_ev_create_t create;
    essh_ev_open_t open;
    essh_ev_close_t close;
    essh_ev_rename_t rename;
    essh_ev_unlink_t unlink;
    essh_ev_link_t link;
    essh_ev_clone_t clone;
    essh_ev_setattrlist_t setattrlist;
    essh_ev_truncate_t truncate;
    essh_ev_uipc_bind_t uipc_bind;
    essh_ev_uipc_connect_t uipc_connect;
    essh_ev_write_t write;
    essh_ev_readdir_t readdir;
    essh_ev_signal_t signal;
} essh_event_t;

typedef struct {
    uint64_t mach_time;
    uint64_t thread_id;
    int64_t time_sec;
    int64_t time_nsec;
    uint64_t seq_num;        /* per-client, per-event-type 序号 */
    uint64_t global_seq_num; /* per-client 全局序号 */
    uint32_t event_type;     /* es_event_type_t 数值（union 判别标签） */
    uint32_t message_version;
    essh_process_t process;  /* 事件发起者（actor） */
    essh_event_t event;
} essh_message_t;

/*
 * 事件回调。msg 指向的数据仅在回调期间有效；Rust 侧必须整体拷贝后才能留存。
 * 回调内只做了字段拷贝，必须立刻返回（不得阻塞 ES 事件线程）。
 */
typedef void (*essh_cb)(void *ctx, const essh_message_t *msg);

/* 返回值均为对应 ES API 的原生返回码（0 = 成功），错误语义由 Rust 侧解释。 */
int essh_client_new(essh_client_t **out, essh_cb cb, void *ctx);
int essh_subscribe(essh_client_t *c, uint32_t event_mask, uint32_t extra_mask);
int essh_mute_path_prefix(essh_client_t *c, const char *path);
void essh_client_delete(essh_client_t *c);

/* AF_INET/AF_INET6 socket 的扁平快照（net-poller 用）。 */
typedef struct {
    int32_t family;   /* AF_INET / AF_INET6 */
    int32_t type;     /* SOCK_STREAM / SOCK_DGRAM */
    int32_t protocol; /* IPPROTO_TCP / IPPROTO_UDP */
    int32_t state;    /* TCP 状态（非 TCP 为 -1） */
    uint32_t lport;
    uint32_t fport;
    char laddr[ESSH_ADDR_CAP];
    char faddr[ESSH_ADDR_CAP];
} essh_socket_t;

/* 枚举指定 pid 的全部 INET/INET6 socket。成功返回 0，*out 为 malloc 数组，
 * 用 essh_free_sockets 释放；无 socket 时返回 0 且 *out=NULL, *count=0。 */
int essh_list_sockets(int32_t pid, essh_socket_t **out, size_t *count);
void essh_free_sockets(essh_socket_t *s, size_t count);

#endif /* ES_SHIM_H */
