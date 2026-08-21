//! 美股行情数据抓取。
//!
//! 数据源优先级: Yahoo Finance chart API → Stooq CSV 兜底。
//! 两者均为公开接口, 无需 API Key; 单只股票抓取失败时跳过,
//! 全部失败才整体报错。

use chrono::TimeZone;
use serde::Deserialize;
use std::time::Duration;

/// 美股七巨头 (Magnificent 7) 代码与公司名。
pub const TICKERS: [(&str, &str); 7] = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"),
    ("NVDA", "NVIDIA"),
    ("META", "Meta"),
    ("TSLA", "Tesla"),
];

/// 单个交易日的数据点。
#[derive(Clone, Debug)]
pub struct DayPoint {
    /// 交易日, 格式 "MM-DD"。
    pub date: String,
    /// 收盘价 (美元)。
    pub close: f64,
}

/// 单只股票近一个月的日线序列。
#[derive(Clone, Debug)]
pub struct Series {
    pub ticker: String,
    pub name: String,
    pub points: Vec<DayPoint>,
}

impl Series {
    /// 相对首日收盘的涨跌幅 (%): (最新 - 首日) / 首日。
    pub fn change_pct(&self) -> f64 {
        let first = self.points.first().map(|p| p.close).unwrap_or(0.0);
        let last = self.points.last().map(|p| p.close).unwrap_or(0.0);
        if first > 0.0 {
            (last / first - 1.0) * 100.0
        } else {
            0.0
        }
    }

    /// 最新收盘价 (美元)。
    pub fn last_close(&self) -> f64 {
        self.points.last().map(|p| p.close).unwrap_or(0.0)
    }

    /// 第一个交易日的日期, 格式 "MM-DD"。
    pub fn first_date(&self) -> &str {
        self.points.first().map(|p| p.date.as_str()).unwrap_or("")
    }

    /// 最后一个交易日的日期, 格式 "MM-DD"。
    pub fn last_date(&self) -> &str {
        self.points.last().map(|p| p.date.as_str()).unwrap_or("")
    }
}

/// 抓取全部七只股票近一个月日线数据。
pub fn fetch_all() -> Result<Vec<Series>, String> {
    let mut series = Vec::new();
    let mut errors = Vec::new();

    for (ticker, name) in TICKERS {
        let points = match fetch_yahoo(ticker).or_else(|e| {
            errors.push(e.clone());
            fetch_stooq(ticker)
        }) {
            Ok(p) => p,
            Err(e) => {
                errors.push(e);
                continue;
            }
        };
        series.push(Series {
            ticker: ticker.to_string(),
            name: name.to_string(),
            points,
        });
    }

    if series.is_empty() {
        return Err(format!(
            "全部数据源均抓取失败: {}",
            errors.join("; ")
        ));
    }
    Ok(series)
}

// ---------- Yahoo Finance ----------

#[derive(Deserialize)]
struct YahooChart {
    chart: YahooChartBody,
}

#[derive(Deserialize)]
struct YahooChartBody {
    result: Vec<YahooChartResult>,
}

#[derive(Deserialize)]
struct YahooChartResult {
    timestamp: Vec<i64>,
    indicators: YahooIndicators,
}

#[derive(Deserialize)]
struct YahooIndicators {
    quote: Vec<YahooQuote>,
}

#[derive(Deserialize)]
struct YahooQuote {
    close: Vec<Option<f64>>,
}

/// 从 Yahoo Finance chart API 抓取近一个月日线。
fn fetch_yahoo(ticker: &str) -> Result<Vec<DayPoint>, String> {
    let url = format!(
        "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d&includePrePost=false&events=div%2Csplits"
    );
    let body = ureq::get(&url)
        .timeout(Duration::from_secs(15))
        .set(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        )
        .call()
        .map_err(|e| format!("yahoo {ticker}: {e}"))?
        .into_string()
        .map_err(|e| format!("yahoo {ticker} read: {e}"))?;

    let chart: YahooChart =
        serde_json::from_str(&body).map_err(|e| format!("yahoo {ticker} parse: {e}"))?;
    let result = chart
        .chart
        .result
        .first()
        .ok_or_else(|| format!("yahoo {ticker}: empty result"))?;

    let empty: Vec<Option<f64>> = Vec::new();
    let closes = result
        .indicators
        .quote
        .first()
        .map(|q| &q.close)
        .unwrap_or(&empty);

    let mut points = Vec::new();
    for (ts, close) in result.timestamp.iter().zip(closes.iter()) {
        if let Some(c) = close {
            if let Some(dt) = chrono::Utc.timestamp_opt(*ts, 0).single() {
                points.push(DayPoint {
                    date: dt.format("%m-%d").to_string(),
                    close: *c,
                });
            }
        }
    }

    if points.len() < 2 {
        return Err(format!("yahoo {ticker}: 数据点不足 ({})", points.len()));
    }
    Ok(points)
}

// ---------- Stooq 兜底 ----------

/// 从 Stooq CSV 接口抓取日线 (格式: Date,Open,High,Low,Close,Volume)。
fn fetch_stooq(ticker: &str) -> Result<Vec<DayPoint>, String> {
    let url = format!("https://stooq.com/q/d/l/?s={}&i=d", ticker.to_lowercase());
    let body = ureq::get(&url)
        .timeout(Duration::from_secs(15))
        .call()
        .map_err(|e| format!("stooq {ticker}: {e}"))?
        .into_string()
        .map_err(|e| format!("stooq {ticker} read: {e}"))?;

    let mut points = Vec::new();
    for line in body.lines().skip(1) {
        let mut it = line.split(',');
        let date = it.next().unwrap_or("").to_string();
        let _ = it.next(); // Open
        let _ = it.next(); // High
        let _ = it.next(); // Low
        let close: f64 = it.next().unwrap_or("").parse().unwrap_or(0.0);
        if close > 0.0 && date.len() >= 10 {
            points.push(DayPoint {
                date: date[5..].to_string(),
                close,
            });
        }
    }

    if points.len() < 2 {
        return Err(format!("stooq {ticker}: 数据点不足 ({})", points.len()));
    }
    Ok(points)
}
