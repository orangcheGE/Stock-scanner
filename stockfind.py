import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
import urllib.parse
from datetime import datetime

# ─────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers(), timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.type_2')
            if not table:
                continue
            for tr in table.select('tr'):
                tds = tr.find_all('td')
                if len(tds) < 5:
                    continue
                a = tr.find('a', href=True)
                if not a:
                    continue
                match = re.search(r'code=(\d{6})', a['href'])
                if match:
                    codes.append(match.group(1))
                    names.append(a.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
            time.sleep(0.3)
        except:
            continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def get_price_data(code, max_pages=30):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages + 1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers(), timeout=10)
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list:
                dfs.append(df_list[0])
        except:
            continue
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가', '고가', '저가', '거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜', '종가']).sort_values('날짜').reset_index(drop=True)

def load_foreign_ratio_all(market="KOSPI", max_pages=40):
    sosok = "0" if market == "KOSPI" else "1"
    ratio_dict = {}
    base_url = (f"https://finance.naver.com/sise/sise_foreign_hold.naver"
                f"?sosok={sosok}")
    try:
        res = requests.get(f"{base_url}&page=1", headers=get_headers(), timeout=10)
        res.encoding = 'euc-kr'
        soup = BeautifulSoup(res.text, 'html.parser')
        pager = soup.select_one('td.pgRR a')
        if pager and 'page=' in pager.get('href',''):
            m = re.search(r'page=(\d+)', pager['href'])
            total_pages = int(m.group(1)) if m else max_pages
        else:
            total_pages = max_pages
        total_pages = min(total_pages, max_pages)
        ratio_dict.update(_parse_foreign_page(soup))
        for page in range(2, total_pages + 1):
            try:
                r = requests.get(f"{base_url}&page={page}",
                                 headers=get_headers(), timeout=8)
                r.encoding = 'euc-kr'
                s = BeautifulSoup(r.text, 'html.parser')
                ratio_dict.update(_parse_foreign_page(s))
                time.sleep(0.15)
            except Exception:
                continue
    except Exception:
        pass
    return ratio_dict

def _parse_foreign_page(soup):
    result = {}
    table = soup.select_one('table.type_2')
    if not table:
        return result
    for tr in table.select('tr'):
        tds = tr.find_all('td')
        if len(tds) < 8:
            continue
        a = tr.find('a', href=True)
        if not a:
            continue
        m = re.search(r'code=(\d{6})', a['href'])
        if not m:
            continue
        code = m.group(1)
        try:
            ratio_txt = tds[7].get_text(strip=True).replace('%','').replace(',','').strip()
            ratio = float(ratio_txt)
            result[code] = ratio
        except (ValueError, IndexError):
            continue
    return result

def _fmt_ratio(ratio: float) -> str:
    if ratio >= 30:   return f"{ratio:.2f}% 🔴고비중"
    elif ratio >= 15: return f"{ratio:.2f}% 🟠중비중"
    elif ratio >= 5:  return f"{ratio:.2f}% 🟡저비중"
    else:             return f"{ratio:.2f}% ⚪미미"

def get_ma5_slope(price_series):
    try:
        ma5 = price_series.rolling(5).mean()
        if len(ma5) < 4:
            return 0
        slope = ma5.iloc[-1] - ma5.iloc[-3]
        pct   = slope / ma5.iloc[-3] * 100 if ma5.iloc[-3] != 0 else 0
        return pct
    except Exception:
        return 0

# ─────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────
def calc_bollinger(series, period=20, std_mult=2):
    ma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    bandwidth = (upper - lower) / ma * 100
    return upper, lower, bandwidth

def calc_cci(df, period=20):
    tp = (df['고가'] + df['저가'] + df['종가']) / 3
    ma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * mad.replace(0, np.nan))


# ─────────────────────────────────────────────
# 점수 기반 신호 결정
# ─────────────────────────────────────────────
def calc_signal_score(last, prev, ichimoku_status,
                      cci_now, cci_prev,
                      disparity,
                      foreign_ratio=0.0,
                      ma5_slope=0, pct_from_52high=0,
                      price_col='종가'):
    score = 0
    detail = {}
    if '상향돌파' in ichimoku_status: s = 3
    elif '하향이탈' in ichimoku_status: s = -3
    elif '상승진입' in ichimoku_status: s = 1
    elif '하락진입' in ichimoku_status: s = -2
    else: s = 0
    score += s
    detail['구름대'] = s

    hist_now = last['MACD_hist']
    hist_prev = prev['MACD_hist']
    macd_slope = hist_now - hist_prev
    if hist_now > 0 and hist_prev <= 0: s = 2
    elif hist_now < 0 and hist_prev >= 0: s = -2
    elif hist_now < 0 and macd_slope > 0: s = 1
    elif hist_now > 0 and macd_slope < 0: s = -1
    else: s = 0
    score += s
    detail['MACD'] = s

    if cci_prev < -100 and cci_now >= -100: s = 2
    elif cci_prev < 0 and cci_now >= 0: s = 1
    elif cci_prev > 0 and cci_now <= 0: s = -1
    elif cci_prev > 100 and cci_now <= 100: s = -2
    else: s = 0
    score += s
    detail['CCI'] = s

    if disparity > 20: s = -3
    elif disparity > 12: s = -2
    elif disparity > 6: s = -1
    elif disparity >= -3: s = 0
    elif disparity >= -8: s = 1
    else: s = 2
    score += s
    detail['이격률'] = s

    vol_ratio = last.get('vol_ratio', np.nan)
    has_turn = (detail['구름대'] != 0 or abs(detail['MACD']) >= 1 or abs(detail['CCI']) >= 1)
    if not pd.isna(vol_ratio):
        if vol_ratio >= 1.5 and has_turn: s = 1
        elif vol_ratio < 0.5: s = -1
        else: s = 0
    else: s = 0
    score += s
    detail['거래량'] = s

    if foreign_ratio >= 30: s = 1
    elif foreign_ratio > 0 and foreign_ratio < 5: s = -1
    else: s = 0
    score += s
    detail['외국인지분'] = s

    if ma5_slope > 0.3 and detail['구름대'] >= 0: s = 1
    elif ma5_slope < -0.3 and detail['구름대'] <= 0: s = -1
    else: s = 0
    score += s
    detail['5MA기울기'] = s

    if pct_from_52high <= -30: s = 1
    else: s = 0
    score += s
    detail['52주위치'] = s

    is_above_cloud   = '구름대 위' in ichimoku_status or '상향돌파' in ichimoku_status
    is_below_cloud   = '구름대 아래' in ichimoku_status or '하향이탈' in ichimoku_status
    is_falling_entry = '하락진입' in ichimoku_status
    is_rising_entry  = '상승진입' in ichimoku_status
    is_inside_cloud  = '내부' in ichimoku_status
    cloud_breakout   = detail['구름대'] == 3
    cloud_breakdown  = detail['구름대'] == -3
    macd_up          = detail['MACD'] >= 1
    macd_down        = detail['MACD'] <= -1
    cci_up           = detail['CCI'] > 0
    cci_down         = detail['CCI'] < 0
    is_high_disp     = disparity > 15
    is_mid_disp      = 6 < disparity <= 15
    is_low_disp      = disparity < -10

    if is_falling_entry: signal = "⚠️ 구름대주의"
    elif (score >= 7 and cloud_breakout and macd_up and cci_up): signal = "🔥 적극매수"
    elif (score >= 4 and not is_high_disp and (cloud_breakout or macd_up or cci_up) and sum([cloud_breakout, macd_up, cci_up]) >= 2): signal = "📈 매수관심"
    elif (score >= 2 and disparity <= 6 and has_turn and not is_falling_entry): signal = "🌱 진입준비"
    elif (is_below_cloud a
