#include "es_shim.h"

#include <EndpointSecurity/EndpointSecurity.h>
#include <stdlib.h>

struct esmvp_client {
    es_client_t *inner;
};

int esmvp_client_new(esmvp_client_t **out, esmvp_open_cb cb, void *ctx) {
    es_handler_block_t handler = ^(es_client_t *client, const es_message_t *msg) {
        (void)client; /* 应答经 msg 令牌由 Rust 侧回指 client，此处无需使用 */
        if (msg->event_type != ES_EVENT_TYPE_AUTH_OPEN) return;
        const es_file_t *file = msg->event.open.file;
        cb(ctx, (const void *)msg, file->path.data, file->path.length,
           msg->event.open.fflag, (uint32_t)file->stat.st_mode);
    };

    es_client_t *client = NULL;
    es_new_client_result_t rc = es_new_client(&client, handler);
    if (rc != ES_NEW_CLIENT_RESULT_SUCCESS) return (int)rc;

    esmvp_client_t *wrapper = calloc(1, sizeof(*wrapper));
    if (!wrapper) {
        es_delete_client(client);
        return (int)ES_NEW_CLIENT_RESULT_ERR_INTERNAL;
    }
    wrapper->inner = client;
    *out = wrapper;
    return (int)ES_NEW_CLIENT_RESULT_SUCCESS;
}

int esmvp_unmute_all_target_paths(esmvp_client_t *c) {
    return (int)es_unmute_all_target_paths(c->inner);
}

int esmvp_invert_target_path_muting(esmvp_client_t *c) {
    return (int)es_invert_muting(c->inner, ES_MUTE_INVERSION_TYPE_TARGET_PATH);
}

int esmvp_target_muting_is_inverted(esmvp_client_t *c) {
    switch (es_muting_inverted(c->inner, ES_MUTE_INVERSION_TYPE_TARGET_PATH)) {
    case ES_MUTE_INVERTED: return 1;
    case ES_MUTE_NOT_INVERTED: return 0;
    default: return -1;
    }
}

int esmvp_mute_target_prefix(esmvp_client_t *c, const char *path) {
    return (int)es_mute_path(c->inner, path, ES_MUTE_PATH_TYPE_TARGET_PREFIX);
}

int esmvp_subscribe_auth_open(esmvp_client_t *c) {
    es_event_type_t events[] = {ES_EVENT_TYPE_AUTH_OPEN};
    return (int)es_subscribe(c->inner, events, 1);
}

int esmvp_respond_open(esmvp_client_t *c, const void *msg, uint32_t flags, bool cache) {
    /* AUTH_OPEN 是 flags 类事件，必须 es_respond_flags_result；
     * 误用 es_respond_auth_result 会整体失败并触发 deadline kill（ObjC 版实测）。 */
    return (int)es_respond_flags_result(c->inner, (const es_message_t *)msg, flags, cache);
}

long esmvp_default_target_mute_count(esmvp_client_t *c) {
    es_muted_paths_t *muted = NULL;
    if (es_muted_paths_events(c->inner, &muted) != ES_RETURN_SUCCESS || !muted) return -1;
    long count = (long)muted->count;
    es_release_muted_paths(muted);
    return count;
}

void esmvp_client_delete(esmvp_client_t *c) {
    if (!c) return;
    if (c->inner) es_delete_client(c->inner);
    free(c);
}
