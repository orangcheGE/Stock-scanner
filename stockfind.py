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
    elif (is_below_cloud and (macd_up or cci_up) and score >= 0): signal = "🔄 바닥탐색"
    elif (is_below_cloud and macd_down and cci_down): signal = "🔻 하락가속"
    elif (score <= -5 and cloud_breakdown and macd_down and cci_down): signal = "🧊 적극매도"
    elif score <= -3: signal = "📉 매도관심"
    elif is_below_cloud and is_low_disp: signal = "🔽 추세하락"
    elif is_above_cloud and is_high_disp: signal = "🔼 추세상승"
    elif is_above_cloud and is_mid_disp and not has_turn: signal = "🛡️ 홀딩유지"
    elif is_inside_cloud: signal = "🌫️ 구름대내부"
    else: signal = "⏸️ 관망"
    return score, signal, detail

# ─────────────────────────────────────────────
# 종목 분석 메인
# ─────────────────────────────────────────────
def analyze_stock(code, name, current_change, foreign_dict=None, fetch_investor=True):
    try:
        df_price = get_price_data(code, max_pages=25)
        if df_price is None or len(df_price) < 80:
            return None
        df = df_price.set_index('날짜').copy()
        df['5MA'] = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['60MA'] = df['종가'].rolling(60).mean()
        high_9 = df['고가'].rolling(9).max()
        low_9 = df['저가'].rolling(9).min()
        df['tenkan_sen'] = (high_9 + low_9) / 2
        high_26 = df['고가'].rolling(26).max()
        low_26 = df['저가'].rolling(26).min()
        df['kijun_sen'] = (high_26 + low_26) / 2
        high_52 = df['고가'].rolling(52).max()
        low_52 = df['저가'].rolling(52).min()
        df['senkou_b_base'] = (high_52 + low_52) / 2
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_Signal']
        df['CCI'] = calc_cci(df)
        df['vol_ratio'] = df['거래량'] / df['거래량'].rolling(20).mean()
        df_future = pd.DataFrame(index=df.index)
        df_future['senkou_a'] = (df['tenkan_sen'] + df['kijun_sen']) / 2
        df_future['senkou_b'] = df['senkou_b_base']
        df_future = df_future.shift(26)
        df_merged = pd.merge(df, df_future, left_index=True, right_index=True, how='left')
        df_final = df_merged.dropna(subset=['senkou_a', 'senkou_b', 'CCI']).copy()
        if len(df_final) < 6:
            return None
        last = df_final.iloc[-1]
        prev = df_final.iloc[-2]
        prev2 = df_final.iloc[-3]
        prev3 = df_final.iloc[-4]
        prev4 = df_final.iloc[-5]
        price_col = '종가'

        def cloud_top(row): return max(row['senkou_a'], row['senkou_b'])
        def cloud_bot(row): return min(row['senkou_a'], row['senkou_b'])
        price_now = last['종가']
        ct_now, cb_now = cloud_top(last), cloud_bot(last)
        above_now, below_now = price_now > ct_now, price_now < cb_now
        breakout_days = None
        if above_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] <= cloud_top(row):
                    breakout_days = days_ago
                    break
        breakdown_days = None
        if below_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] >= cloud_bot(row):
                    breakdown_days = days_ago
                    break
        if above_now: ichimoku_status = f"🔥 상향돌파({breakout_days}일전)" if breakout_days is not None else "📈 구름대 위"
        elif below_now: ichimoku_status = f"🧊 하향이탈({breakdown_days}일전)" if breakdown_days is not None else "📉 구름대 아래"
        else:
            prior_rows = [prev, prev2, prev3, prev4]
            was_above = any(r['종가'] > cloud_top(r) for r in prior_rows)
            was_below = any(r['종가'] < cloud_bot(r) for r in prior_rows)
            if was_above and not was_below: ichimoku_status = "⚠️ 구름대하락진입"
            elif was_below and not was_above: ichimoku_status = "🌱 구름대상승진입"
            else: ichimoku_status = "🌫️ 구름대 내부"
        def ma_cross(l, p, ma_col):
            if p[price_col] <= p[ma_col] and l[price_col] > l[ma_col]: return "🔥GC"
            if p[price_col] >= p[ma_col] and l[price_col] < l[ma_col]: return "🧊DC"
            return "📈↑" if l[price_col] > l[ma_col] else "📉↓"
        ma_text = f"5:{ma_cross(last,prev,'5MA')} 20:{ma_cross(last,prev,'20MA')} 60:{ma_cross(last,prev,'60MA')}"
        
        cci_now, cci_prev = last['CCI'], prev['CCI']
        cci_val = round(cci_now, 1)
        if cci_prev < -100 and cci_now >= -100: cci_display = f"{cci_val} 🟢과매도탈출"
        elif cci_prev < 0 and cci_now >= 0: cci_display = f"{cci_val} 🔵제로크로스"
        elif cci_prev > 100 and cci_now <= 100: cci_display = f"{cci_val} 🟡과매수탈출"
        elif cci_prev > 0 and cci_now <= 0: cci_display = f"{cci_val} 🔴제로데드"
        elif cci_now > 100: cci_display = f"{cci_val} ⚡과매수"
        elif cci_now < -100: cci_display = f"{cci_val} 💧과매도"
        else: cci_display = f"{cci_val} ➖중립"

        vol_r = round(last['vol_ratio'], 1) if not pd.isna(last['vol_ratio']) else 1.0
        vol_display = f"{vol_r}배 📈" if vol_r >= 2.0 else f"{vol_r}배 📉" if vol_r < 0.5 else f"{vol_r}배"
        
        disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        
        try:
            high_52 = df['종가'].rolling(252).max().iloc[-1]
            pct_52high = round(((last['종가'] - high_52) / high_52) * 100, 1) if not pd.isna(high_52) and high_52 != 0 else 0.0
        except:
            pct_52high = 0.0
            
        ma5_slope = get_ma5_slope(df['종가'])
        
        if fetch_investor and foreign_dict is not None:
            foreign_ratio = foreign_dict.get(code, 0.0)
            investor_display = _fmt_ratio(foreign_ratio) if foreign_ratio > 0 else "-"
        else:
            foreign_ratio, investor_display = 0.0, "-"
            
        score, signal, detail = calc_signal_score(
            last, prev, ichimoku_status, cci_now, cci_prev, disparity,
            foreign_ratio=foreign_ratio, ma5_slope=ma5_slope, pct_from_52high=pct_52high
        )
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        return [
            code, name, current_change,
            int(last['종가']), disparity_fmt,
            score, signal,
            ichimoku_status, ma_text,
            cci_display, vol_display,
            investor_display,
            chart_url
        ]
    except Exception as e:
        return None

# ─────────────────────────────────────────────
# 스타일 데이터프레임 표시
# ─────────────────────────────────────────────
COLUMNS = ['코드', '종목명', '등락률', '현재가', '이격률',
           '총점', '신호',
           '일목(일봉)', 'MA크로스',
           'CCI', '거래량',
           '외국인지분율',
           '차트']

def style_signal(val):
    v = str(val)
    if '적극매수' in v: return 'color:white;background-color:#b71c1c;font-weight:bold'
    if '매수관심' in v: return 'color:#ef5350;font-weight:bold'
    if '진입준비' in v: return 'color:#ff8f00;font-weight:bold'
    if '바닥탐색' in v: return 'color:#8d6e63;font-weight:bold'
    if '홀딩유지' in v: return 'color:#2e7d32;font-weight:bold'
    if '추세상승' in v: return 'color:#558b2f'
    if '구름대내부' in v: return 'color:#78909c'
    if '구름대주의' in v: return 'color:white;background-color:#e65100;font-weight:bold'
    if '하락가속' in v: return 'color:white;background-color:#4a148c;font-weight:bold'
    if '추세하락' in v: return 'color:#1565c0;font-weight:bold'
    if '매도관심' in v: return 'color:#42a5f5;font-weight:bold'
    if '적극매도' in v: return 'color:white;background-color:#0d47a1;font-weight:bold'
    return 'color:#9e9e9e'

def style_ichimoku(val):
    v = str(val)
    if '상향돌파' in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '하향이탈' in v: return 'color:white;background-color:#1565c0;font-weight:bold'
    if '하락진입' in v: return 'color:white;background-color:#e65100;font-weight:bold'
    if '상승진입' in v: return 'color:#ff8f00;font-weight:bold'
    if '구름대 위' in v: return 'color:#ef5350'
    if '구름대 아래'in v: return 'color:#64b5f6'
    return 'color:#9e9e9e'

def style_score(val):
    try:
        v = int(val)
        if v >= 5: return 'color:white;background-color:#c62828;font-weight:bold'
        if v >= 2: return 'color:#ef5350;font-weight:bold'
        if v >= -1: return 'color:#9e9e9e'
        if v >= -4: return 'color:#42a5f5;font-weight:bold'
        return 'color:white;background-color:#1565c0;font-weight:bold'
    except:
        return ''

def style_cci(val):
    v = str(val)
    if '과매도탈출' in v: return 'color:#43a047;font-weight:bold'
    if '제로크로스' in v and '🔵' in v: return 'color:#1e88e5;font-weight:bold'
    if '제로데드' in v: return 'color:#e53935;font-weight:bold'
    if '과매수탈출' in v: return 'color:#fb8c00;font-weight:bold'
    if '과매수' in v: return 'color:#e53935'
    if '과매도' in v: return 'color:#43a047'
    return ''

def style_pct(val):
    v = str(val).strip()
    if not v or v == '-': return ''
    try:
        if v.startswith('+'): return 'color:#ef5350'
        if v.startswith('-'): return 'color:#42a5f5'
        num = float(v.replace('%', '').replace(',', ''))
        if num > 0: return 'color:#ef5350'
        if num < 0: return 'color:#42a5f5'
    except Exception:
        pass
    return ''

def compress_display(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    ichi_map = {"🔥 최근 상향돌파": "🔥상향돌파", "🧊 최근 하향이탈": "🧊하향이탈", "📈 구름대 위": "📈위", "📉 구름대 아래": "📉아래", "🌫️ 구름대 진입": "🌫️진입"}
    d['일목(일봉)'] = d['일목(일봉)'].replace(ichi_map)
    def compress_ma(v):
        parts = str(v).split(' ')
        out = []
        for p in parts:
            if ':' in p:
                num, sym = p.split(':', 1)
                short = sym[:2] if len(sym) >= 2 else sym
                out.append(f"{num}{short}")
        return ' '.join(out) if out else v
    d['MA크로스'] = d['MA크로스'].apply(compress_ma)
    d['신호'] = d['신호'].str.strip()
    return d

def style_investor(val):
    v = str(val)
    if '고비중' in v: return 'color:#b71c1c;font-weight:bold'
    if '중비중' in v: return 'color:#e65100;font-weight:bold'
    if '저비중' in v: return 'color:#f9a825'
    if '미미' in v: return 'color:#9e9e9e'
    return ''

def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다.")
        return
    disp = compress_display(dataframe)
    dynamic_height = (len(disp) + 1) * 35 + 3
    
    styled = (
        disp.style
        .map(style_signal,   subset=['신호'])
        .map(style_ichimoku, subset=['일목(일봉)'])
        .map(style_cci,      subset=['CCI'])
        .map(style_score,    subset=['총점'])
        .map(style_pct,      subset=['등락률', '이격률'])
        .map(lambda x: ('color:#b71c1c;font-weight:bold' if '🔥' in str(x) else
                        'color:#0d47a1;font-weight:bold' if '🧊' in str(x) else
                        'color:#ef5350' if '📈' in str(x) else
                        'color:#42a5f5' if '📉' in str(x) else ''),
             subset=['MA크로스'])
        .map(lambda x: ('color:#ef5350' if '📈' in str(x) else
                        'color:#64b5f6' if '📉' in str(x) else ''),
             subset=['거래량'])
        .map(style_investor, subset=['외국인지분율'])
    )
    
    col_cfg = {
        "코드": st.column_config.TextColumn("코드"),
        "총점": st.column_config.NumberColumn("점수"),
        "등락률": st.column_config.TextColumn("등락"),
        "이격률": st.column_config.TextColumn("이격"),
        "거래량": st.column_config.TextColumn("거래량"),
        "차트": st.column_config.LinkColumn("차트", display_text="📊"),
        "신호": st.column_config.TextColumn("신호"),
        "일목(일봉)": st.column_config.TextColumn("일목"),
        "MA크로스": st.column_config.TextColumn("MA"),
        "CCI": st.column_config.TextColumn("CCI"),
        "종목명": st.column_config.TextColumn("종목명"),
        "현재가": st.column_config.NumberColumn("현재가"),
        "외국인지분율": st.column_config.TextColumn("외국인%"),
    }
    
    st.dataframe(
        styled,
        use_container_width=True,
        height=dynamic_height,
        column_config=col_cfg,
        hide_index=True
    )

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("🛡️ 스마트 데이터 스캐너 v4.1")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
st.sidebar.markdown("---")
use_investor = st.sidebar.checkbox(
    "📡 외인/기관 순매수 수집",
    value=True,
    help="종목당 추가 요청 1회 → 분석 시간 약 30% 증가"
)
st.sidebar.markdown("---")
st.sidebar.markdown("""
**📊 12단계 신호 기준**
**[매수 계열]**
| 신호 | 의미 |
|:---|:---|
| 🔥 적극매수 | 구름대돌파+MACD·CCI 동시↑ |
| 📈 매수관심 | 전환신호 2개↑, 이격률 양호 |
| 🌱 진입준비 | 전환신호 1개, 타이밍 양호 |
| 🔄 바닥탐색 | 구름대 아래+회복 조짐 |

**[보유/중립 계열]**
| 신호 | 의미 |
|:---|:---|
| 🛡️ 홀딩유지 | 구름대 위, 이격률 적당 |
| 🔼 추세상승 | 많이 오름, 신규진입 주의 |
| 🌫️ 구름대내부 | 방향 불명확 횡보 |
| ⏸️ 관망 | 신호 없음 |

**[위험/하락 계열]**
| 신호 | 의미 |
|:---|:---|
| ⚠️ 구름대주의 | 위→구름대 하락진입 |
| 🔻 하락가속 | 구름대아래+MACD·CCI↓ |
| 🔽 추세하락 | 구름대아래+이격률↓ |
| 📉 매도관심 | 하락전환 총점≤-3 |
| 🧊 적극매도 | 이탈+동시하락 총점≤-5 |
""")
start_btn = st.sidebar.button("🚀 분석 시작")

st.subheader("📊 진단 및 필터링")
c1, c2, c3, c4, c5, c6 = st.columns(6)
total_metric, buy_metric, entry_metric = c1.empty(), c2.empty(), c3.empty()
caution_metric, fall_metric, sell_metric = c4.empty(), c5.empty(), c6.empty()
total_metric.metric("전체", "0개")
buy_metric.metric("매수계열", "0개")
entry_metric.metric("진입준비", "0개")
caution_metric.metric("구름대주의","0개")
fall_metric.metric("하락계열", "0개")
sell_metric.metric("매도관심↓", "0개")

fb1,fb2,fb3,fb4,fb5,fb6,fb7,fb8 = st.columns(8)
if 'filter' not in st.session_state:
    st.session_state.filter = "전체"
if fb1.button("🔄전체", use_container_width=True): st.session_state.filter = "전체"
if fb2.button("🔥📈매수", use_container_width=True): st.session_state.filter = "매수"
if fb3.button("🌱진입준비", use_container_width=True): st.session_state.filter = "진입준비"
if fb4.button("🔄바닥탐색", use_container_width=True): st.session_state.filter = "바닥탐색"
if fb5.button("🛡️홀딩", use_container_width=True): st.session_state.filter = "홀딩"
if fb6.button("⚠️구름주의", use_container_width=True): st.session_state.filter = "구름대주의"
if fb7.button("🔻하락가속", use_container_width=True): st.session_state.filter = "하락가속"
if fb8.button("📉🧊매도", use_container_width=True): st.session_state.filter = "매도"
st.markdown("---")
result_title = st.empty()
main_result_area = st.empty()

def update_metrics(df):
    buy_kw = '적극매수|매수관심'
    fall_kw = '하락가속|추세하락|적극매도'
    sell_kw = '매도관심|적극매도'
    total_metric.metric("전체", f"{len(df)}개")
    buy_metric.metric("매수계열", f"{len(df[df['신호'].str.contains(buy_kw, regex=True)])}개")
    entry_metric.metric("진입준비", f"{len(df[df['신호'].str.contains('진입준비|바닥탐색', regex=True)])}개")
    caution_metric.metric("구름대주의", f"{len(df[df['신호'].str.contains('구름대주의')])}개")
    fall_metric.metric("하락계열", f"{len(df[df['신호'].str.contains(fall_kw, regex=True)])}개")
    sell_metric.metric("매도관심↓", f"{len(df[df['신호'].str.contains(sell_kw, regex=True)])}개")

def apply_filter(df, f):
    if f == "매수": return df[df['신호'].str.contains("적극매수|매수관심", regex=True)]
    elif f == "진입준비": return df[df['신호'].str.contains("진입준비")]
    elif f == "바닥탐색": return df[df['신호'].str.contains("바닥탐색")]
    elif f == "홀딩": return df[df['신호'].str.contains("홀딩유지|추세상승", regex=True)]
    elif f == "구름대주의": return df[df['신호'].str.contains("구름대주의")]
    elif f == "하락가속": return df[df['신호'].str.contains("하락가속|추세하락", regex=True)]
    elif f == "매도": return df[df['신호'].str.contains("매도")]
    return df

if start_btn:
    st.session_state.filter = "전체"
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        st.session_state['df_all'] = pd.DataFrame()
        foreign_dict = {}
        if use_investor:
            with st.spinner(f"📡 {market} 외국인 보유 비율 수집 중... (최초 1회, 약 20~30초)"):
                foreign_dict = load_foreign_ratio_all(market=market, max_pages=40)
            st.info(f"✅ 외국인 지분율 {len(foreign_dict):,}개 종목 수집 완료")
        progress_bar = st.progress(0, text="분석 시작...")
        for i, (_, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'],
                                foreign_dict=foreign_dict, fetch_investor=use_investor)
            if res:
                results.append(res)
                df_all = pd.DataFrame(results, columns=COLUMNS)
                df_all = df_all.sort_values('총점', ascending=False).reset_index(drop=True)
                st.session_state['df_all'] = df_all
                update_metrics(df_all)
                display_df = apply_filter(df_all, st.session_state.filter)
                result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
                with main_result_area:
                    show_styled_dataframe(display_df)
            progress_bar.progress((i + 1) / len(market_df), text=f"분석 중: {row['종목명']} ({i+1}/{len(market_df)})")
        progress_bar.empty()
        st.success("✅ 분석 완료!")

if not start_btn and 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = apply_filter(df, st.session_state.filter)
    update_metrics(df)
    result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
    with main_result_area:
        show_styled_dataframe(display_df)
    if not display_df.empty:
        # 이메일 전송 기능은 RSI 컬럼이 없어졌으므로, 해당 컬럼을 제외하고 생성합니다.
        email_summary = display_df[['종목명', '현재가', '총점', '신호', '일목(일봉)']].to_string(index=False)
        encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
        mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
        st.markdown(
            f'<a href="{mailto_url}" target="_self" style="text-decoration:none;">'
            f'<div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;'
            f'text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>',
            unsafe_allow_html=True
        )
elif 'df_all' not in st.session_state:
    with main_result_area:
        st.info("왼쪽 사이드바에서 '분석 시작' 버튼을 눌러주세요.")
