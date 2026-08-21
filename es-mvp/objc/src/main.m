// esmvp — 基于 es_invert_muting 的目录级 AUTH_OPEN 管控最小验证（见 SPEC.md）。
//
// 行为：
//   无 --watch：inversion 生效且 target-mute 为空 → 内核抑制全部 AUTH_OPEN（自动放行）。
//   有 --watch：仅目标位于 watch 目录下的 AUTH_OPEN 被投递；png/jpg（按扩展名 MIME）→ DENY，其余 ALLOW。
//
// ES 调用序列（ESClient.h 注释约束：invert 前不得有 AUTH 订阅）：
//   es_new_client → (留档默认 mute set) → es_unmute_all_target_paths → es_invert_muting(TARGET_PATH)
//   → es_muting_inverted 自检 → es_mute_path(TARGET_PREFIX, watch目录) → es_subscribe(AUTH_OPEN)

#import <EndpointSecurity/EndpointSecurity.h>
#import <Foundation/Foundation.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <limits.h>
#import <signal.h>
#import <stdatomic.h>
#import <sys/stat.h>

static es_client_t *g_client = NULL;
static _Atomic uint64_t g_received = 0;
static _Atomic uint64_t g_allowed = 0;
static _Atomic uint64_t g_denied = 0;
static _Atomic uint64_t g_respondError = 0;
static BOOL g_verbose = NO;
static BOOL g_cacheAllow = NO;  // --cache：ALLOW 响应带 cache=true，启用内核授权缓存

// 按扩展名 → UTType → preferredMIMEType 判定；命中 image/png|image/jpeg 返回对应 MIME，否则 nil。
// 不读文件内容：无 TCC/FDA 依赖，handler 内 O(1)。已知局限：改名可绕过（MVP 接受）。
static NSString *DeniedMIMEType(const char *cpath) {
  NSString *ext = [@(cpath).pathExtension lowercaseString];
  if (ext.length == 0) return nil;
  NSString *mime = [UTType typeWithFilenameExtension:ext].preferredMIMEType;
  if ([mime isEqualToString:@"image/png"] || [mime isEqualToString:@"image/jpeg"]) return mime;
  return nil;
}

static void PrintStats(NSString *tag) {
  fprintf(stdout,
          "[esmvp][stats] %s received=%llu allowed=%llu denied=%llu respond_error=%llu\n",
          tag.UTF8String, atomic_load(&g_received), atomic_load(&g_allowed),
          atomic_load(&g_denied), atomic_load(&g_respondError));
  fflush(stdout);
}

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    setvbuf(stdout, NULL, _IOLBF, 0);  // 行缓冲：重定向到文件时崩溃不丢日志
    NSMutableArray<NSString *> *watchDirs = [NSMutableArray array];
    int statsInterval = 10;

    for (int i = 1; i < argc; i++) {
      NSString *arg = @(argv[i]);
      if ([arg isEqualToString:@"--watch"] && i + 1 < argc) {
        [watchDirs addObject:@(argv[++i])];
      } else if ([arg isEqualToString:@"--verbose"]) {
        g_verbose = YES;
      } else if ([arg isEqualToString:@"--cache"]) {
        g_cacheAllow = YES;
      } else if ([arg isEqualToString:@"--stats-interval"] && i + 1 < argc) {
        // 注意：勿用 MAX(1, atoi(argv[++i]))——系统头文件的朴素 MAX 宏会对参数求值两次，
        // ++i 二次自增后 atoi(argv[argc]=NULL) 段错误（2026-08-10 实测崩溃）
        int v = atoi(argv[++i]);
        statsInterval = v > 0 ? v : 1;
      } else {
        fprintf(stderr,
                "usage: sudo %s [--watch <dir>]... [--verbose] [--cache] [--stats-interval <sec>]\n",
                argv[0]);
        return 2;
      }
    }

    if (@available(macOS 13.0, *)) {
    } else {
      fprintf(stderr, "[esmvp][fatal] es_invert_muting requires macOS 13.0+\n");
      return 1;
    }

    es_handler_block_t handler = ^(es_client_t *client, const es_message_t *msg) {
      if (msg->event_type != ES_EVENT_TYPE_AUTH_OPEN) return;
      atomic_fetch_add(&g_received, 1);

      const es_file_t *file = msg->event.open.file;
      const char *path = file->path.data ?: "";

      BOOL deny = NO;
      NSString *deniedMIME = nil;
      // 只裁决普通文件；目录等非普通文件直接放行
      if (S_ISREG(file->stat.st_mode)) {
        deniedMIME = DeniedMIMEType(path);
        if (deniedMIME) deny = YES;
      }

      // AUTH_OPEN 必须用 es_respond_flags_result 应答（ESClient.h 明示：
      // 用 es_respond_auth_result 应答 flags 类事件会失败）。flags=0 即 DENY，
      // 放行时回传事件原 fflag。用错 API 会导致响应全部失败 → deadline 超期 → 被 ES kill。
      // --cache：ALLOW 时 cache=true 让内核缓存（进程×文件×flags）授权结果，
      // 同类事件不再上送；DENY 永不缓存（防误封固化 + 每次拦截可观测）。
      uint32_t flags = deny ? 0 : msg->event.open.fflag;
      BOOL cache = g_cacheAllow && !deny;
      if (es_respond_flags_result(client, msg, flags, cache) != ES_RESPOND_RESULT_SUCCESS)
        atomic_fetch_add(&g_respondError, 1);
      if (deny)
        atomic_fetch_add(&g_denied, 1);
      else
        atomic_fetch_add(&g_allowed, 1);

      if (g_verbose || deny)
        fprintf(stdout, "[esmvp][event] %s path=%s mime=%s cache=%d\n", deny ? "DENY " : "ALLOW",
                path, deniedMIME ? deniedMIME.UTF8String : "-", cache);
    };

    // 1. es_new_client
    es_new_client_result_t rc = es_new_client(&g_client, handler);
    if (rc != ES_NEW_CLIENT_RESULT_SUCCESS) {
      fprintf(stderr,
              "[esmvp][fatal] es_new_client failed rc=%d — 需要 root + "
              "com.apple.developer.endpoint-security.client entitlement（embedded "
              "provisionprofile）\n",
              rc);
      return 1;
    }

    // 2. 留档默认 target mute set，然后清空（否则 inversion 后默认集被"选中"）
    es_muted_paths_t *muted = NULL;
    if (es_muted_paths_events(g_client, &muted) == ES_RETURN_SUCCESS && muted) {
      fprintf(stdout, "[esmvp] default target mute set: %zu entr%s\n", muted->count,
              muted->count == 1 ? "y" : "ies");
      for (size_t i = 0; i < muted->count; i++)
        fprintf(stdout, "[esmvp]   default[%zu] %.*s\n", i,
                (int)muted->paths[i].path.length, muted->paths[i].path.data);
      es_release_muted_paths(muted);
    }
    if (es_unmute_all_target_paths(g_client) != ES_RETURN_SUCCESS)
      fprintf(stderr, "[esmvp][warn] es_unmute_all_target_paths failed\n");

    // 3. 反转 target path muting
    if (es_invert_muting(g_client, ES_MUTE_INVERSION_TYPE_TARGET_PATH) != ES_RETURN_SUCCESS) {
      fprintf(stderr, "[esmvp][fatal] es_invert_muting failed\n");
      return 1;
    }

    // 4. 自检
    es_mute_inverted_return_t inv =
        es_muting_inverted(g_client, ES_MUTE_INVERSION_TYPE_TARGET_PATH);
    if (inv != ES_MUTE_INVERTED) {
      fprintf(stderr, "[esmvp][fatal] es_muting_inverted check failed rc=%d\n", inv);
      return 1;
    }

    // 5. 应用 watch 目录（inversion 语义下：mute = 只接收这些路径的事件）
    NSMutableArray<NSString *> *normalized = [NSMutableArray array];
    for (NSString *dir in watchDirs) {
      char resolved[PATH_MAX];
      if (!realpath(dir.stringByExpandingTildeInPath.fileSystemRepresentation, resolved)) {
        fprintf(stderr, "[esmvp][fatal] realpath failed for %s: %s\n", dir.UTF8String,
                strerror(errno));
        return 1;
      }
      NSMutableString *rule = [NSMutableString stringWithUTF8String:resolved];
      if (![rule hasSuffix:@"/"]) [rule appendString:@"/"];  // 目录规则强制尾斜杠
      if (es_mute_path(g_client, rule.fileSystemRepresentation,
                       ES_MUTE_PATH_TYPE_TARGET_PREFIX) != ES_RETURN_SUCCESS) {
        fprintf(stderr, "[esmvp][fatal] es_mute_path failed for %s\n", rule.UTF8String);
        return 1;
      }
      [normalized addObject:rule];
    }

    // 6. 最后订阅 AUTH_OPEN
    es_event_type_t events[] = {ES_EVENT_TYPE_AUTH_OPEN};
    if (es_subscribe(g_client, events, 1) != ES_RETURN_SUCCESS) {
      fprintf(stderr, "[esmvp][fatal] es_subscribe(AUTH_OPEN) failed\n");
      return 1;
    }

    if (normalized.count == 0)
      fprintf(stdout, "[esmvp] mode=mute-all (无 --watch：全部 AUTH_OPEN 在内核侧抑制)\n");
    else
      fprintf(stdout, "[esmvp] mode=watch-only, dirs=%s\n",
              [normalized componentsJoinedByString:@", "].UTF8String);
    fflush(stdout);

    // 7. 周期统计 + 信号收尾
    dispatch_source_t timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
                                                     dispatch_get_main_queue());
    dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, 0),
                              (uint64_t)statsInterval * NSEC_PER_SEC, 0);
    dispatch_source_set_event_handler(timer, ^{
      PrintStats(@"interval");
    });
    dispatch_resume(timer);

    signal(SIGINT, SIG_IGN);
    signal(SIGTERM, SIG_IGN);
    dispatch_source_t sigInt = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGINT, 0,
                                                      dispatch_get_main_queue());
    dispatch_source_t sigTerm = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0,
                                                       dispatch_get_main_queue());
    dispatch_source_set_event_handler(sigInt, ^{
      PrintStats(@"final(SIGINT)");
      exit(0);
    });
    dispatch_source_set_event_handler(sigTerm, ^{
      PrintStats(@"final(SIGTERM)");
      exit(0);
    });
    dispatch_resume(sigInt);
    dispatch_resume(sigTerm);

    dispatch_main();
  }
}
