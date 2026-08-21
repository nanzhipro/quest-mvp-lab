/*
 * es_shim — libEndpointSecurity 的极简 C 垫片。
 *
 * 职责仅限两件事，均无法安全地用纯 Rust 表达：
 *  1. es_message_t 的字段提取（其布局大且随 message version 演进，
 *     手写 repr(C) 风险远高于收益）；
 *  2. es_handler_block_t 的 blocks 语法桥接为普通 C 函数指针。
 *
 * 所有策略逻辑（静音规则、裁决、缓存）都在 Rust 侧，本文件不含任何业务判断。
 */
#ifndef ES_SHIM_H
#define ES_SHIM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct esmvp_client esmvp_client_t; /* 不透明句柄，包裹 es_client_t */

/*
 * AUTH_OPEN 事件回调。path/path_len 引用 es_string_token_t（不保证 NUL 结尾），
 * msg 为不透明应答令牌；二者仅在回调期间有效。
 */
typedef void (*esmvp_open_cb)(void *ctx, const void *msg, const char *path,
                              size_t path_len, uint32_t fflag, uint32_t st_mode);

/* 返回值均为对应 ES API 的原生返回码（0 = 成功），错误语义由 Rust 侧解释。 */
int esmvp_client_new(esmvp_client_t **out, esmvp_open_cb cb, void *ctx);
int esmvp_unmute_all_target_paths(esmvp_client_t *c);
int esmvp_invert_target_path_muting(esmvp_client_t *c);
/* 1 = 已反转，0 = 未反转，-1 = 查询失败 */
int esmvp_target_muting_is_inverted(esmvp_client_t *c);
int esmvp_mute_target_prefix(esmvp_client_t *c, const char *path);
int esmvp_subscribe_auth_open(esmvp_client_t *c);
/* 应答一条 AUTH_OPEN：flags=0 即拒绝；cache 控制是否写入内核授权缓存 */
int esmvp_respond_open(esmvp_client_t *c, const void *msg, uint32_t flags, bool cache);
/* 默认 target mute set 条目数（诊断用），<0 为查询失败 */
long esmvp_default_target_mute_count(esmvp_client_t *c);
void esmvp_client_delete(esmvp_client_t *c);

#endif /* ES_SHIM_H */
