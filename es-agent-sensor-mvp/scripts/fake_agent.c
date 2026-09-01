// e2e 假 agent：在 agent 工作目录内编译运行的最小行为序列发生器。
// 由 scripts/e2e.sh 编译到 $AGENT_DIR/bin/agent（ad-hoc 签名，executable 命中
// agent 目录前缀 → actor_process 归因）。行为覆盖：fork/exec、文件增删改/
// rename/rmdir/truncate/chmod、目录外文件、Unix socket bind/connect、TCP 连接。
#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

static void path_join(char *out, size_t cap, const char *dir, const char *name) {
    snprintf(out, cap, "%s/%s", dir, name);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: agent <workdir>\n");
        return 2;
    }
    const char *dir = argv[1];
    char p[4096], p2[4096];

    // 派生子进程（spawn + exec）
    pid_t c = fork();
    if (c == 0) {
        execl("/bin/echo", "echo", "child-exec", (char *)NULL);
        _exit(1);
    }
    int st = 0;
    waitpid(c, &st, 0);

    // 目录内文件：create + write(close modified) + read(open) + move + delete
    path_join(p, sizeof p, dir, "config.yaml");
    FILE *f = fopen(p, "w");
    fputs("v1\n", f);
    fclose(f);
    f = fopen(p, "a");
    fputs("v2\n", f);
    fclose(f);
    char buf[64];
    f = fopen(p, "r");
    if (f && fgets(buf, sizeof buf, f)) fclose(f);
    path_join(p2, sizeof p2, dir, "config.yaml.bak");
    rename(p, p2);
    unlink(p2);

    // mkdir + rmdir（UNLINK is_dir=true）
    path_join(p, sizeof p, dir, "subdir");
    mkdir(p, 0755);
    rmdir(p);

    // truncate + chmod（setattr）
    path_join(p, sizeof p, dir, "trunc.txt");
    f = fopen(p, "w");
    fputs("123456", f);
    fclose(f);
    truncate(p, 2);
    chmod(p, 0600);
    unlink(p);

    // 目录外文件（agent 对外部数据的操作 → actor_process 归因）
    f = fopen("/tmp/es-sensor-e2e-outside.txt", "w");
    fputs("out", f);
    fclose(f);
    unlink("/tmp/es-sensor-e2e-outside.txt");

    // IPC：Unix socket bind + connect（跨进程：子进程 listen，父进程 connect）
    path_join(p, sizeof p, dir, "agent.sock");
    unlink(p);
    pid_t ipc_child = fork();
    if (ipc_child == 0) {
        int srv = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un un;
        memset(&un, 0, sizeof un);
        un.sun_family = AF_UNIX;
        snprintf(un.sun_path, sizeof un.sun_path, "%s", p);
        if (bind(srv, (struct sockaddr *)&un, sizeof un) == 0 && listen(srv, 1) == 0) {
            int a = accept(srv, NULL, NULL);
            close(a);
        }
        close(srv);
        _exit(0);
    }
    usleep(300000); // 等子进程完成 bind+listen
    {
        struct sockaddr_un un;
        memset(&un, 0, sizeof un);
        un.sun_family = AF_UNIX;
        snprintf(un.sun_path, sizeof un.sun_path, "%s", p);
        int cli = socket(AF_UNIX, SOCK_STREAM, 0);
        int rc = connect(cli, (struct sockaddr *)&un, sizeof un);
        fprintf(stderr, "uipc connect rc=%d errno=%d\n", rc, rc ? errno : 0);
        close(cli);
    }
    waitpid(ipc_child, &st, 0);
    unlink(p);

    // NET：先 sleep 让 net-poller 对本进程建立"零 socket"基线，
    // 再保持 4s 的 TCP 连接（基线后出现 → net_connect；关闭 → net_close）
    sleep(2);
    struct addrinfo hints, *res = NULL;
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo("example.com", "443", &hints, &res) == 0 && res) {
        int t = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
        int rc = connect(t, res->ai_addr, res->ai_addrlen);
        fprintf(stderr, "tcp connect rc=%d errno=%d\n", rc, rc ? errno : 0);
        if (rc == 0) {
            sleep(4);
        }
        close(t);
        freeaddrinfo(res);
    } else {
        fprintf(stderr, "getaddrinfo failed\n");
    }
    return 0;
}
