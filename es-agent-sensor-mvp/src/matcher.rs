//! Agent 工作目录集合：realpath 规范化 + 边界安全的路径前缀匹配。
//!
//! 双向匹配（计划 §1.3）：
//! - `actor_process`：事件进程节点带 agent 注解（proctree 提供）；
//! - `target_path`：事件目标路径位于任一 agent 工作目录下。

use std::path::{Path, PathBuf};

use crate::error::SensorError;
use crate::schema::MatchKind;

#[derive(Debug, Clone)]
pub struct Agent {
    pub id: String,
    /// realpath 规范化后的工作目录（不含尾部 `/`）。
    pub workdir: String,
}

#[derive(Debug, Default)]
pub struct AgentSet {
    agents: Vec<Agent>,
}

impl AgentSet {
    /// 从 CLI 规格构建：每个目录做 realpath（canonicalize）规范化。
    /// 目录不存在或无法规范化时报配置错误。
    pub fn new(specs: &[(String, PathBuf)]) -> Result<Self, SensorError> {
        let mut agents = Vec::with_capacity(specs.len());
        for (id, dir) in specs {
            let canonical = std::fs::canonicalize(dir).map_err(|e| {
                SensorError::Config(format!(
                    "agent `{id}` 目录 {} 无法规范化: {e}",
                    dir.display()
                ))
            })?;
            agents.push(Agent {
                id: id.clone(),
                workdir: normalize(&canonical),
            });
        }
        Ok(Self { agents })
    }

    /// 测试用：跳过 realpath，直接使用给定目录（仍做尾斜杠规范化）。
    #[cfg(test)]
    pub fn for_test(id: &str, dir: &str) -> Self {
        Self {
            agents: vec![Agent {
                id: id.to_owned(),
                workdir: dir.trim_end_matches('/').to_owned(),
            }],
        }
    }

    pub fn agents(&self) -> &[Agent] {
        &self.agents
    }

    /// 路径前缀匹配（防 `/foo/bar` 误伤 `/foo/bar2`）：
    /// 命中条件为 `path == dir` 或 `path` 以 `dir + '/'` 开头。
    pub fn match_path(&self, path: &str) -> Option<&Agent> {
        self.agents.iter().find(|a| path_within(&a.workdir, path))
    }

    /// executable 路径 → agent_id（proctree 注解器的基础匹配）。
    pub fn agent_for_executable(&self, path: &str) -> Option<String> {
        self.match_path(path).map(|a| a.id.clone())
    }

    /// program（executable + argv）→ agent_id（proctree 注解器）。
    /// executable 命中优先；否则任一 argv 元素命中（解释型脚本场景：venv
    /// python 的真实可执行文件解析到 agent 目录外，脚本路径在 argv 里）。
    pub fn agent_for_program(&self, program: &crate::schema::Program) -> Option<String> {
        self.agent_for_executable(&program.executable).or_else(|| {
            program
                .argv
                .iter()
                .find_map(|a| self.agent_for_executable(a))
        })
    }

    /// 汇总 actor/target 两侧命中结果为最终 match 类型与 agent 归属。
    /// actor 注解优先决定归属 agent；两侧命中不同 agent 时记录 actor 侧。
    pub fn combine(
        &self,
        actor_agent_id: Option<&str>,
        target_path: Option<&str>,
    ) -> Option<(Agent, MatchKind)> {
        let actor_hit = actor_agent_id.and_then(|id| self.agents.iter().find(|a| a.id == id));
        let path_hit = target_path.and_then(|p| self.match_path(p));
        match (actor_hit, path_hit) {
            (Some(a), Some(_)) => Some((a.clone(), MatchKind::Both)),
            (Some(a), None) => Some((a.clone(), MatchKind::ActorProcess)),
            (None, Some(a)) => Some((a.clone(), MatchKind::TargetPath)),
            (None, None) => None,
        }
    }
}

fn normalize(p: &Path) -> String {
    p.to_string_lossy().trim_end_matches('/').to_owned()
}

fn path_within(dir: &str, path: &str) -> bool {
    path == dir
        || (path.len() > dir.len() && path.starts_with(dir) && path.as_bytes()[dir.len()] == b'/')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefix_boundary_does_not_overmatch() {
        let set = AgentSet::for_test("a", "/foo/bar");
        assert!(set.match_path("/foo/bar").is_some()); // 目录本身
        assert!(set.match_path("/foo/bar/x").is_some()); // 子路径
        assert!(set.match_path("/foo/bar2").is_none()); // 前缀边界
        assert!(set.match_path("/foo/ba").is_none());
        assert!(set.match_path("/foo").is_none());
    }

    #[test]
    fn trailing_slash_normalized() {
        let set = AgentSet::for_test("a", "/foo/bar/");
        assert!(set.match_path("/foo/bar/x").is_some());
        assert!(set.match_path("/foo/bar").is_some());
    }

    #[test]
    fn multi_agent_matching() {
        let mut set = AgentSet::for_test("hermes", "/opt/hermes");
        set.agents.push(Agent {
            id: "claude".into(),
            workdir: "/opt/claude".into(),
        });
        assert_eq!(set.match_path("/opt/hermes/db").unwrap().id, "hermes");
        assert_eq!(set.match_path("/opt/claude/db").unwrap().id, "claude");
        assert!(set.match_path("/opt/other/db").is_none());
        assert_eq!(
            set.agent_for_executable("/opt/hermes/bin/run"),
            Some("hermes".to_owned())
        );
    }

    #[test]
    fn combine_bidirectional_match() {
        let set = AgentSet::for_test("hermes", "/opt/hermes");
        // 仅 actor
        let (a, m) = set.combine(Some("hermes"), Some("/etc/hosts")).unwrap();
        assert_eq!(m, MatchKind::ActorProcess);
        assert_eq!(a.id, "hermes");
        // 仅 path（其他进程触碰 agent 数据）
        let (_, m) = set.combine(None, Some("/opt/hermes/config.yaml")).unwrap();
        assert_eq!(m, MatchKind::TargetPath);
        // 双命中
        let (_, m) = set
            .combine(Some("hermes"), Some("/opt/hermes/config.yaml"))
            .unwrap();
        assert_eq!(m, MatchKind::Both);
        // 双不命中
        assert!(set.combine(None, Some("/var/log/x")).is_none());
        // actor 注解的 agent 不在集合中（理论上不发生，兜底）
        assert!(set.combine(Some("ghost"), Some("/var/log/x")).is_none());
    }

    #[test]
    fn real_canonicalization() {
        let tmp = std::env::temp_dir().join("es-sensor-matcher-test");
        std::fs::create_dir_all(&tmp).unwrap();
        let set = AgentSet::new(&[("t".into(), tmp.clone())]).unwrap();
        // canonicalize 解析 /tmp → /private/tmp 等链接
        let canonical = std::fs::canonicalize(&tmp).unwrap();
        assert_eq!(set.agents()[0].workdir, canonical.to_string_lossy());
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn canonicalize_failure_is_config_error() {
        let err =
            AgentSet::new(&[("bad".into(), PathBuf::from("/nonexistent-dir-xyz"))]).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("bad"), "错误应含 agent id: {msg}");
        assert!(msg.contains("无法规范化"), "错误应说明原因: {msg}");
    }
}
