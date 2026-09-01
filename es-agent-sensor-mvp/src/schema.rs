//! AgentEvent 归一化 Schema（`agent-event/1.0`）：Actor–Operation–Target 模型。
//!
//! 信封（schema_version/event_id/seq/sensor）与语义载荷（agent/actor/operation/target/context）
//! 分离；`target` 为 serde internally-tagged 多态（`kind` 判别）。

use serde::Serialize;

pub const SCHEMA_VERSION: &str = "agent-event/1.0";
pub const SENSOR_NAME: &str = "es-agent-sensor";

#[derive(Debug, Clone, Serialize)]
pub struct AgentEvent {
    pub schema_version: &'static str,
    pub event_id: String,
    /// RFC3339 纳秒（UTC，`Z` 结尾）。
    pub time: String,
    pub seq: Seq,
    pub sensor: Sensor,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent: Option<AgentRef>,
    pub actor: Actor,
    pub operation: Operation,
    pub target: Target,
    pub context: Context,
}

#[derive(Debug, Clone, Serialize)]
pub struct Seq {
    /// ES per-client 全局序号（global_seq_num）。
    pub global: u64,
    /// ES per-client、per-event-type 序号（seq_num）。
    pub local: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct Sensor {
    pub name: &'static str,
    pub version: &'static str,
    pub pid: u32,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MatchKind {
    ActorProcess,
    TargetPath,
    Both,
}

#[derive(Debug, Clone, Serialize)]
pub struct AgentRef {
    pub id: String,
    pub workdir: String,
    #[serde(rename = "match")]
    pub match_kind: MatchKind,
}

#[derive(Debug, Clone, Serialize)]
pub struct Actor {
    pub pid: i32,
    pub pidversion: u64,
    pub ppid: i32,
    pub program: Program,
    pub cred: Cred,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub origin: Option<Origin>,
    pub responsible_pid: i32,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, Hash)]
pub struct Program {
    pub executable: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub argv: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signing_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub team_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cdhash: Option<String>,
    pub is_platform_binary: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Cred {
    pub ruid: u32,
    pub euid: u32,
    pub rgid: u32,
    pub egid: u32,
    pub auid: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct Origin {
    pub agent_root_pid: i32,
    pub agent_root_executable: String,
    /// 从 agent 根到 actor 的可执行文件链（含两端）。
    pub ancestry: Vec<String>,
    pub depth_from_agent_root: u32,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Category {
    File,
    Process,
    Ipc,
    Net,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Verb {
    Exec,
    Spawn,
    Exit,
    Create,
    Open,
    Write,
    Move,
    Delete,
    Link,
    Clone,
    Setattr,
    Truncate,
    IpcBind,
    IpcConnect,
    NetConnect,
    NetClose,
    Readdir,
    Signal,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Outcome {
    Completed,
    /// net-poller 轮询快照，区别于实时事件。
    Snapshot,
}

#[derive(Debug, Clone, Serialize)]
pub struct EsEventRef {
    #[serde(rename = "type")]
    pub type_name: String,
    pub id: u32,
    pub message_version: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct Operation {
    pub category: Category,
    pub verb: Verb,
    pub outcome: Outcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub es_event: Option<EsEventRef>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FileStat {
    pub ino: u64,
    pub mode: u32,
    pub size: i64,
    pub uid: u32,
    pub gid: u32,
    pub mtime: i64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Target {
    File {
        path: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        dst_path: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        access: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        stat: Option<FileStat>,
        /// create：目标此前是否已存在；rename：dst 是否覆盖了已存在文件。
        #[serde(skip_serializing_if = "Option::is_none")]
        existed: Option<bool>,
        /// delete/link/clone：目标是否目录。
        #[serde(skip_serializing_if = "Option::is_none")]
        is_dir: Option<bool>,
        /// setattr：变更的 attr 位图对应的属性名列表。
        #[serde(skip_serializing_if = "Option::is_none")]
        attrs: Option<Vec<String>>,
        /// open：原始 fflag，便于下游精确判断。
        #[serde(skip_serializing_if = "Option::is_none")]
        fflag: Option<i32>,
    },
    Process {
        pid: i32,
        pidversion: u64,
        #[serde(skip_serializing_if = "Option::is_none")]
        program: Option<Program>,
        #[serde(skip_serializing_if = "Option::is_none")]
        exit_stat: Option<i32>,
    },
    Socket {
        domain: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        path: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        socket_type: Option<i32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        protocol: Option<i32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        mode: Option<u32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        peer_process: Option<PeerProcess>,
    },
    Endpoint {
        proto: String,
        local: String,
        remote: String,
    },
}

impl Target {
    /// 事件目标路径（file/socket），供 matcher 做 target_path 匹配。
    pub fn primary_path(&self) -> Option<&str> {
        match self {
            Target::File { path, .. } => Some(path),
            Target::Socket { path, .. } => path.as_deref(),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct PeerProcess {
    pub pid: i32,
    pub executable: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Context {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_workdir_hit: Option<String>,
    pub mach_time: u64,
    pub thread_id: u64,
}

/// epoch（秒 + 纳秒）→ RFC3339 纳秒（UTC）。
/// 不引 chrono/time：用标准 civil-from-days 算法（Howard Hinnant）。
pub fn rfc3339_nanos(sec: i64, nsec: i64) -> String {
    let days = sec.div_euclid(86_400);
    let secs_of_day = sec.rem_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    let hh = secs_of_day / 3600;
    let mm = secs_of_day % 3600 / 60;
    let ss = secs_of_day % 60;
    format!("{y:04}-{m:02}-{d:02}T{hh:02}:{mm:02}:{ss:02}.{nsec:09}Z")
}

/// days since 1970-01-01 → (year, month, day)，公历。
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097); // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365; // [0, 399]
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32; // [1, 12]
    (if m <= 2 { y + 1 } else { y }, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_event() -> AgentEvent {
        AgentEvent {
            schema_version: SCHEMA_VERSION,
            event_id: "01K5EXAMPLEULID".into(),
            time: rfc3339_nanos(1_788_163_200, 123_456_789),
            seq: Seq {
                global: 19116,
                local: 42,
            },
            sensor: Sensor {
                name: SENSOR_NAME,
                version: "0.1.0",
                pid: 1234,
            },
            agent: Some(AgentRef {
                id: "hermes".into(),
                workdir: "/Users/nanzhi/.hermes".into(),
                match_kind: MatchKind::Both,
            }),
            actor: Actor {
                pid: 71634,
                pidversion: 2715978,
                ppid: 71583,
                program: Program {
                    executable: "/opt/agent/venv/bin/python3".into(),
                    argv: vec!["python3".into(), "-m".into(), "tui.entry".into()],
                    signing_id: Some("com.nanzhipro.hermes".into()),
                    team_id: Some("ABCDE12345".into()),
                    cdhash: Some("ab12".into()),
                    is_platform_binary: false,
                },
                cred: Cred {
                    ruid: 501,
                    euid: 501,
                    rgid: 20,
                    egid: 20,
                    auid: 501,
                },
                origin: Some(Origin {
                    agent_root_pid: 71583,
                    agent_root_executable: "/opt/agent/bin/hermes".into(),
                    ancestry: vec!["/bin/zsh".into(), "/opt/agent/bin/hermes".into()],
                    depth_from_agent_root: 1,
                }),
                responsible_pid: 71583,
            },
            operation: Operation {
                category: Category::File,
                verb: Verb::Write,
                outcome: Outcome::Completed,
                es_event: Some(EsEventRef {
                    type_name: "ES_EVENT_TYPE_NOTIFY_CLOSE".into(),
                    id: 12,
                    message_version: 10,
                }),
            },
            target: Target::File {
                path: "/Users/nanzhi/.hermes/config.yaml".into(),
                dst_path: None,
                access: None,
                stat: Some(FileStat {
                    ino: 12345,
                    mode: 0o100644,
                    size: 512,
                    uid: 501,
                    gid: 20,
                    mtime: 1_788_163_000,
                }),
                existed: None,
                is_dir: None,
                attrs: None,
                fflag: None,
            },
            context: Context {
                agent_workdir_hit: Some("/Users/nanzhi/.hermes/config.yaml".into()),
                mach_time: 4_731_552_479_268,
                thread_id: 6_011_254,
            },
        }
    }

    #[test]
    fn schema_json_field_names() {
        let v = serde_json::to_value(sample_event()).unwrap();
        assert_eq!(v["schema_version"], "agent-event/1.0");
        assert_eq!(v["seq"]["global"], 19116);
        assert_eq!(v["seq"]["local"], 42);
        assert_eq!(v["sensor"]["name"], "es-agent-sensor");
        assert_eq!(v["agent"]["match"], "both");
        assert_eq!(v["actor"]["program"]["argv"][2], "tui.entry");
        assert_eq!(v["actor"]["cred"]["auid"], 501);
        assert_eq!(v["actor"]["origin"]["depth_from_agent_root"], 1);
        assert_eq!(v["operation"]["category"], "file");
        assert_eq!(v["operation"]["verb"], "write");
        assert_eq!(v["operation"]["outcome"], "completed");
        assert_eq!(
            v["operation"]["es_event"]["type"],
            "ES_EVENT_TYPE_NOTIFY_CLOSE"
        );
        assert_eq!(v["operation"]["es_event"]["id"], 12);
        assert_eq!(v["target"]["kind"], "file");
        assert_eq!(v["target"]["stat"]["mode"], 0o100644);
        assert_eq!(v["context"]["thread_id"], 6_011_254);
        // None 字段不序列化
        assert!(v["target"].get("dst_path").is_none());
        assert!(v["target"].get("access").is_none());
    }

    #[test]
    fn target_polymorphic_kinds() {
        let p = Target::Process {
            pid: 1,
            pidversion: 2,
            program: None,
            exit_stat: Some(0),
        };
        let v = serde_json::to_value(&p).unwrap();
        assert_eq!(v["kind"], "process");
        assert_eq!(v["exit_stat"], 0);

        let s = Target::Socket {
            domain: "unix".into(),
            path: Some("/tmp/a.sock".into()),
            socket_type: None,
            protocol: None,
            mode: None,
            peer_process: None,
        };
        let v = serde_json::to_value(&s).unwrap();
        assert_eq!(v["kind"], "socket");
        assert_eq!(v["domain"], "unix");

        let e = Target::Endpoint {
            proto: "tcp4".into(),
            local: "127.0.0.1:18789".into(),
            remote: "142.250.0.1:443".into(),
        };
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["kind"], "endpoint");
        assert_eq!(v["proto"], "tcp4");
    }

    #[test]
    fn rfc3339_known_timestamps() {
        assert_eq!(rfc3339_nanos(0, 0), "1970-01-01T00:00:00.000000000Z");
        assert_eq!(
            rfc3339_nanos(1_609_459_200, 0),
            "2021-01-01T00:00:00.000000000Z"
        );
        assert_eq!(
            rfc3339_nanos(1_788_220_800, 123_456_789),
            "2026-09-01T00:00:00.123456789Z"
        );
        // 闰年边界：2024-02-29
        assert_eq!(
            rfc3339_nanos(1_709_164_800, 0),
            "2024-02-29T00:00:00.000000000Z"
        );
        // 负数（epoch 之前）
        assert_eq!(
            rfc3339_nanos(-1, 999_000_000),
            "1969-12-31T23:59:59.999000000Z"
        );
    }

    #[test]
    fn verb_and_category_lowercase() {
        assert_eq!(serde_json::to_value(Verb::IpcBind).unwrap(), "ipc_bind");
        assert_eq!(
            serde_json::to_value(Verb::NetConnect).unwrap(),
            "net_connect"
        );
        assert_eq!(serde_json::to_value(Category::Ipc).unwrap(), "ipc");
        assert_eq!(serde_json::to_value(Outcome::Snapshot).unwrap(), "snapshot");
        assert_eq!(
            serde_json::to_value(MatchKind::ActorProcess).unwrap(),
            "actor_process"
        );
    }
}
