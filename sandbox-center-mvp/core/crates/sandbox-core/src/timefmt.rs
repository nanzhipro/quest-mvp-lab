//! Minimal UTC timestamp formatting (no chrono dependency).
//!
//! Audit records need a wall-clock stamp that is unambiguous across machines,
//! so everything is rendered as `YYYY-MM-DDTHH:MM:SS.mmmZ` from a Unix epoch in
//! milliseconds. The inverse civil-date algorithm is Howard Hinnant's
//! `civil_from_days`.

use std::time::{SystemTime, UNIX_EPOCH};

pub fn now_unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

/// Convert days since 1970-01-01 into a `(year, month, day)` civil date.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

/// `1_789_000_000_123` → `"2026-09-14T03:06:40.123Z"`.
pub fn iso8601_utc(unix_ms: u64) -> String {
    let seconds = (unix_ms / 1000) as i64;
    let millis = (unix_ms % 1000) as u32;
    let days = seconds.div_euclid(86_400);
    let seconds_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = seconds_of_day / 3600;
    let minute = (seconds_of_day % 3600) / 60;
    let second = seconds_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millis:03}Z")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_epoch_and_known_instants() {
        assert_eq!(iso8601_utc(0), "1970-01-01T00:00:00.000Z");
        assert_eq!(iso8601_utc(1_000), "1970-01-01T00:00:01.000Z");
        // 2000-02-29 (leap day in a century-leap year).
        assert_eq!(iso8601_utc(951_782_400_000), "2000-02-29T00:00:00.000Z");
        // Cross-checked with `date -u -r 1789000000 "+%Y-%m-%dT%H:%M:%S"`.
        assert_eq!(iso8601_utc(1_789_000_000_123), "2026-09-10T00:26:40.123Z");
    }

    #[test]
    fn now_is_recent() {
        let now = now_unix_ms();
        assert!(now > 1_700_000_000_000, "unexpected clock: {now}");
        assert_eq!(iso8601_utc(now).len(), 24);
    }
}
