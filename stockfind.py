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

def get_price_data(code, max_pages=60):  # 주봉 분석을 위해 기본 수집 페이지를 60(약 600일, 120주)으로 확대
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
# 점수 기반 신호 결정 (주봉 일목 도입 및 수정)
# ─────────────────────────────────────────────
def calc_signal_score(last, prev, ichimoku_status, w_ichimoku_status, cci_now, cci_prev):
    score = 0
    detail = {}
    
    # 1. 일목균형표 점수 (일봉)
    if '상향돌파' in ichimoku_status: s_ichi = 3
    elif '하향이탈' in ichimoku_status: s_ichi = -3
    elif '상승진입' in ichimoku_status: s_ichi = 1
    elif '하락진입' in ichimoku_status: s_ichi = -2
    else: s_ichi = 0
    score += s_ichi
    detail['구름대(일)'] = s_ichi

    # 2. 일목균형표 점수 (주봉) - 가중치 증가
    if '상향돌파' in w_ichimoku_status: s_w_ichi = 4  # 주봉 돌파는 매우 강한 추세 전환 신호
    elif '하향이탈' in w_ichimoku_status: s_w_ichi = -4
    elif '구름대 위' in w_ichimoku_status: s_w_ichi = 2
    elif '구름대 아래' in w_ichimoku_status: s_w_ichi = -2
    elif '상승진입' in w_ichimoku_status: s_w_ichi = 1
    elif '하락진입' in w_ichimoku_status: s_w_ichi = -2
    else: s_w_ichi = 0
    score += s_w_ichi
    detail['구름대(주)'] = s_w_ichi

    # 3. MACD + CCI 모멘텀 통합 점수
    # MACD 점수 계산
    hist_now = last['MACD_hist']
    hist_prev = prev['MACD_hist']
    macd_slope = hist_now - hist_prev
    if hist_now > 0 and hist_prev <= 0: s_macd = 2
    elif hist_now < 0 and hist_prev >= 0: s_macd = -2
    elif hist_now < 0 and macd_slope > 0: s_macd = 1
    elif hist_now > 0 and macd_slope < 0: s_macd = -1
    else: s_macd = 0
    
    # CCI 점수 계산
    if cci_prev < -100 and cci_now >= -100: s_cci = 2
    elif cci_prev < 0 and cci_now >= 0: s_cci = 1
    elif cci_prev > 0 and cci_now <= 0: s_cci = -1
    elif cci_prev > 100 and cci_now <= 100: s_cci = -2
    else: s_cci = 0
    
    # 모멘텀 점수 통합 (MACD, CCI)
    s_momentum = 0
    if s_macd > 0 and s_cci > 0:       # 둘 다 상승 신호
        s_momentum = max(s_macd, s_cci)
    elif s_macd < 0 and s_cci < 0:     # 둘 다 하락 신호
        s_momentum = min(s_macd, s_cci)
    elif s_macd != 0 and s_cci == 0:   # MACD 신호만 존재
        s_momentum = s_macd
    elif s_macd == 0 and s_cci != 0:   # CCI 신호만 존재
        s_momentum = s_cci
    # 신호가 엇갈리는 경우는 0점 처리
    score += s_momentum
    detail['모멘텀'] = s_momentum

    # --- 신호 결정 로직 (주봉 돌파 반영) ---
    is_above_cloud   = '구름대 위' in ichimoku_status or '상향돌파' in ichimoku_status or '구름대 위' in w_ichimoku_status or '상향돌파' in w_ichimoku_status
    is_below_cloud   = '구름대 아래' in ichimoku_status or '하향이탈' in ichimoku_status or '구름대 아래' in w_ichimoku_status or '하향이탈' in w_ichimoku_status
    is_falling_entry = '하락진입' in ichimoku_status or '구름대하락진입' in ichimoku_status or '구름대하락진입' in w_ichimoku_status
    
    cloud_breakout   = '상향돌파' in ichimoku_status or '상향돌파' in w_ichimoku_status
    cloud_breakdown  = '하향이탈' in ichimoku_status or '하향이탈' in w_ichimoku_status
    momentum_up      = detail['모멘텀'] >= 1
    momentum_down    = detail['모멘텀'] <= -1
    has_turn         = cloud_breakout or cloud_breakdown or momentum_up or momentum_down
    
    disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
    is_high_disp     = disparity > 15
    is_low_disp      = disparity < -10
    
    # 주봉 일목 구름대 최근 돌파 여부 판단
    is_weekly_breakout = '상향돌파' in w_ichimoku_status

    if is_falling_entry: signal = "⚠️ 구름대주의"
    elif is_weekly_breakout and momentum_up: signal = "🚀 주간돌파!"  # 주간 일목 돌파 최우선 강세 신호
    elif (score >= 5 and cloud_breakout and momentum_up): signal = "🔥 적극매수"
    elif (score >= 3 and not is_high_disp and (cloud_breakout or momentum_up)): signal = "📈 매수관심"
    elif (score >= 1 and disparity <= 6 and has_turn and not is_falling_entry): signal = "🌱 진입준비"
    elif (is_below_cloud and momentum_up and score >= 0): signal = "🔄 바닥탐색"
    elif (is_below_cloud and momentum_down): signal = "🔻 하락가속"
    elif (score <= -5 and cloud_breakdown and momentum_down): signal = "🧊 적극매도"
    elif score <= -3: signal = "📉 매도관심"
    elif is_below_cloud and is_low_disp: signal = "🔽 추세하락"
    elif is_above_cloud and is_high_disp: signal = "🔼 추세상승"
    elif is_above_cloud and not has_turn: signal = "🛡️ 홀딩유지"
    elif '내부' in ichimoku_status or '내부' in w_ichimoku_status: signal = "🌫️ 구름대내부"
    else: signal = "⏸️ 관망"
        
    return score, signal, detail

# ─────────────────────────────────────────────
# 종목 분석 메인 (주봉 일목 분석 모듈 신설)
# ─────────────────────────────────────────────
def analyze_stock(code, name, current_change, foreign_dict=None, fetch_investor=True):
    try:
        # 데이터 수집 (주봉 연산을 위해 기본 60페이지 확보)
        df_price = get_price_data(code, max_pages=60)
        if df_price is None or len(df_price) < 80:
            return None
        
        # ─── 1. 일봉 지표 계산 ───
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
        
        # --- 일봉 일목 설명용 텍스트 생성 ---
        price_now = last['종가']
        ct_now, cb_now = max(last['senkou_a'], last['senkou_b']), min(last['senkou_a'], last['senkou_b'])
        above_now, below_now = price_now > ct_now, price_now < cb_now
        
        breakout_days = None
        if above_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] <= max(row['senkou_a'], row['senkou_b']):
                    breakout_days = days_ago
                    break
                    
        breakdown_days = None
        if below_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] >= min(row['senkou_a'], row['senkou_b']):
                    breakdown_days = days_ago
                    break

        if above_now: ichimoku_status = f"🔥 상향돌파({breakout_days}일전)" if breakout_days is not None else "📈 구름대 위"
        elif below_now: ichimoku_status = f"🧊 하향이탈({breakdown_days}일전)" if breakdown_days is not None else "📉 구름대 아래"
        else:
            prior_rows = [prev, prev2, prev3, prev4]
            was_above = any(r['종가'] > max(r['senkou_a'], r['senkou_b']) for r in prior_rows)
            was_below = any(r['종가'] < min(r['senkou_a'], r['senkou_b']) for r in prior_rows)
            if was_above and not was_below: ichimoku_status = "⚠️ 구름대하락진입"
            elif was_below and not was_above: ichimoku_status = "🌱 구름대상승진입"
            else: ichimoku_status = "🌫️ 구름대 내부"
            
        # ─── 2. 주봉 지표 및 주봉 일목 구름대 계산 ───
        df_w_final = None
        w_ichimoku_status = "-"
        
        # 주간(Weekly) 캔들 리샘플링 생성
        df_w = df_price.resample('W', on='날짜').agg({
            '종가': 'last',
            '고가': 'max',
            '저가': 'min',
            '거래량': 'sum'
        }).dropna()
        
        if len(df_w) >= 53: # 최소 52주 데이터 필요
            w_high_9 = df_w['고가'].rolling(9).max()
            w_low_9 = df_w['저가'].rolling(9).min()
            df_w['tenkan_sen'] = (w_high_9 + w_low_9) / 2
            
            w_high_26 = df_w['고가'].rolling(26).max()
            w_low_26 = df_w['저가'].rolling(26).min()
            df_w['kijun_sen'] = (w_high_26 + w_low_26) / 2
            
            w_high_52 = df_w['고가'].rolling(52).max()
            w_low_52 = df_w['저가'].rolling(52).min()
            df_w['senkou_b_base'] = (w_high_52 + w_low_52) / 2
            
            df_w_future = pd.DataFrame(index=df_w.index)
            df_w_future['senkou_a'] = (df_w['tenkan_sen'] + df_w['kijun_sen']) / 2
            df_w_future['senkou_b'] = df_w['senkou_b_base']
            df_w_future = df_w_future.shift(26)
            
            df_w_merged = pd.merge(df_w, df_w_future, left_index=True, right_index=True, how='left')
            df_w_final = df_w_merged.dropna(subset=['senkou_a', 'senkou_b']).copy()
            
            if len(df_w_final) >= 5:
                w_last = df_w_final.iloc[-1]
                w_prev = df_w_final.iloc[-2]
                w_prev2 = df_w_final.iloc[-3]
                w_prev3 = df_w_final.iloc[-4]
                w_prev4 = df_w_final.iloc[-5]
                
                w_price_now = w_last['종가']
                w_ct_now = max(w_last['senkou_a'], w_last['senkou_b'])
                w_cb_now = min(w_last['senkou_a'], w_last['senkou_b'])
                w_above_now = w_price_now > w_ct_now
                w_below_now = w_price_now < w_cb_now
                
                w_breakout_weeks = None
                if w_above_now:
                    for weeks_ago, row in enumerate([w_prev, w_prev2, w_prev3, w_prev4], start=1):
                        if row['종가'] <= max(row['senkou_a'], row['senkou_b']):
                            w_breakout_weeks = weeks_ago
                            break
                            
                w_breakdown_weeks = None
                if w_below_now:
                    for weeks_ago, row in enumerate([w_prev, w_prev2, w_prev3, w_prev4], start=1):
                        if row['종가'] >= min(row['senkou_a'], row['senkou_b']):
                            w_breakdown_weeks = weeks_ago
                            break
                            
                if w_above_now: 
                    w_ichimoku_status = f"🔥 상향돌파({w_breakout_weeks}주전)" if w_breakout_weeks is not None else "📈 구름대 위"
                elif w_below_now: 
                    w_ichimoku_status = f"🧊 하향이탈({w_breakdown_weeks}주전)" if w_breakdown_weeks is not None else "📉 구름대 아래"
                else:
                    w_prior_rows = [w_prev, w_prev2, w_prev3, w_prev4]
                    w_was_above = any(r['종가'] > max(r['senkou_a'], r['senkou_b']) for r in w_prior_rows)
                    w_was_below = any(r['종가'] < min(r['senkou_a'], r['senkou_b']) for r in w_prior_rows)
                    if w_was_above and not w_was_below: w_ichimoku_status = "⚠️ 구름대하락진입"
                    elif w_was_below and not w_was_above: w_ichimoku_status = "🌱 구름대상승진입"
                    else: w_ichimoku_status = "🌫️ 구름대 내부"
        else:
            w_ichimoku_status = "데이터부족"

        # ─── 3. 기타 보조지표 가공 ───
        def ma_cross(l, p, ma_col):
            if p['종가'] <= p[ma_col] and l['종가'] > l[ma_col]: return "🔥GC"
            if p['종가'] >= p[ma_col] and l['종가'] < l[ma_col]: return "🧊DC"
            return "📈↑" if l['종가'] > l[ma_col] else "📉↓"
        
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
        
        if fetch_investor and foreign_dict is not None:
            foreign_ratio = foreign_dict.get(code, 0.0)
            investor_display = _fmt_ratio(foreign_ratio) if foreign_ratio > 0 else "-"
        else:
            investor_display = "-"
            
        # --- 점수 및 최종 신호 계산 ---
        score, signal, detail = calc_signal_score(
            last, prev, ichimoku_status, w_ichimoku_status, cci_now, cci_prev
        )
        
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        
        return [
            code, name, current_change,
            int(last['종가']), disparity_fmt,
            score, signal,
            ichimoku_status, w_ichimoku_status, ma_text,
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
           '일목(일봉)', '일목(주봉)', 'MA크로스',
           'CCI', '거래량',
           '외국인지분율',
           '차트']

def style_signal(val):
    v = str(val)
    if '주간돌파' in v: return 'color:white;background-color:#d32f2f;font-weight:bold;' # 주간 돌파 강렬한 레드 테두리/배경
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
        if v >= 0: return 'color:#9e9e9e'
        if v >= -3: return 'color:#42a5f5;font-weight:bold'
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
    ichi_map = {
        "🔥 최근 상향돌파": "🔥상향돌파", "🧊 최근 하향이탈": "🧊하향이탈", 
        "📈 구름대 위": "📈위", "📉 구름대 아래": "📉아래", "🌫️ 구름대 진입": "🌫️진입",
        "🌫️ 구름대 내부": "🌫️내부"
    }
    d['일목(일봉)'] = d['일목(일봉)'].replace(ichi_map)
    d['일목(주봉)'] = d['일목(주봉)'].replace(ichi_map)
    
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
        .map(style_ichimoku, subset=['일목(일봉)', '일목(주봉)'])
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
        "일목(일봉)": st.column_config.TextColumn("일목(일)"),
        "일목(주봉)": st.column_config.TextColumn("일목(주)"),
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
st.title("🛡️ 스마트 데이터 스캐너 v4.3 (주봉 일목 도입 및 신호 고도화)")
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
**📊 13단계 신호 기준**

**[매수 계열]**
| 신호 | 의미 |
|:---|:---|
| 🚀 주간돌파! | 주봉 구름대 돌파 + 상승 모멘텀 |
| 🔥 적극매수 | 구름대돌파+모멘텀↑ |
| 📈 매수관심 | 전환신호, 이격률 양호 |
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
| 🔻 하락가속 | 구름대아래+모멘텀↓ |
| 🔽 추세하락 | 구름대아래+이격률↓ |
| 📉 매도관심 | 하락전환 총점≤-3 |
| 🧊 적극매도 | 이탈+모멘텀↓ 총점≤-5 |
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
    buy_kw = '적극매수|매수관심|주간돌파'
    fall_kw = '하락가속|추세하락|적극매도'
    sell_kw = '매도관심|적극매도'
    total_metric.metric("전체", f"{len(df)}개")
    buy_metric.metric("매수계열", f"{len(df[df['신호'].str.contains(buy_kw, regex=True)])}개")
    entry_metric.metric("진입준비", f"{len(df[df['신호'].str.contains('진입준비|바닥탐색', regex=True)])}개")
    caution_metric.metric("구름대주의", f"{len(df[df['신호'].str.contains('구름대주의')])}개")
    fall_metric.metric("하락계열", f"{len(df[df['신호'].str.contains(fall_kw, regex=True)])}개")
    sell_metric.metric("매도관심↓", f"{len(df[df['신호'].str.contains(sell_kw, regex=True)])}개")

def apply_filter(df, f):
    if f == "매수": return df[df['신호'].str.contains("적극매수|매수관심|주간돌파", regex=True)]
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
        email_summary = display_df[['종목명', '현재가', '총점', '신호', '일목(일봉)', '일목(주봉)']].to_string(index=False)
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
