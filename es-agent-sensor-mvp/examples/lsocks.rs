//! 诊断工具：对指定 pid 调用 essh_list_sockets 并打印结果。
//! 用法：lsocks <pid>
fn main() {
    let pid: i32 = std::env::args()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .expect("usage: lsocks <pid>");
    match es_agent_sensor::ffi::list_sockets(pid) {
        Ok(socks) => {
            println!("count={}", socks.len());
            for s in &socks {
                println!(
                    "family={} type={} proto={} state={} {}:{} -> {}:{}",
                    s.family,
                    s.sock_type,
                    s.protocol,
                    s.state,
                    s.laddr_str(),
                    s.lport,
                    s.faddr_str(),
                    s.fport
                );
            }
        }
        Err(rc) => println!("essh_list_sockets rc={rc}"),
    }
}
