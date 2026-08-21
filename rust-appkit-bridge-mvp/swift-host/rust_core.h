/* rust_core.h — 手写 C 头文件（cua_driver_abi.h 的最小化版）。
   由 swift-host/build.sh 在链接时传入 swiftc。 */
#ifndef RUST_CORE_H
#define RUST_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int64_t rust_core_add(int64_t a, int64_t b);

/* 返回堆分配字符串，调用方须传回 rust_core_free_string */
const char *rust_core_greeting(void);

void rust_core_free_string(char *p);

/* x * 3 后交给 cb；cb 为 NULL 时返回 -1 */
int64_t rust_core_apply(int64_t x, int64_t (*cb)(int64_t));

#ifdef __cplusplus
}
#endif

#endif /* RUST_CORE_H */
