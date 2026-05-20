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

# ---------------------------------------------
# 헬퍼 함수
# ---------------------------------------------

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
    """
    https://finance.naver.com/sise/sise_foreign_hold.naver
    페이지에서 전체 외국인 보유 비율을 한 번에 수집
    """
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
    """외국인 지분율 표시 문자열"""
    if ratio >= 30:   return f"{ratio:.2f}% 🔴고비중"
    elif ratio >= 15: return f"{ratio:.2f}% 🟠중비중"
    elif ratio >= 5:  return f"{ratio:.2f}% 🟡저비중"
    else:             return f"{ratio:.2f}% ⚪미미"

def get_ma5_slope(price_series):
    """5MA 기울기"""
    try:
        ma5 = price_series.rolling(5).mean()
        if len(ma5) < 4:
            return 0, "➖"
        slope = ma5.iloc[-1] - ma5.iloc[-3]
        pct   = slope / ma5.iloc[-3] * 100 if ma5.iloc[-3] != 0 else 0
        if pct > 0.5:    return pct, f"↗↗급등({round(pct,1)}%)"
        elif pct > 0.1:  return pct, f"↗상승({round(pct,1)}%)"
        elif pct < -0.5: return pct, f"↘↘급락({round(pct,1)}%)"
        elif pct < -0.1: return pct, f"↘하락({round(pct,1)}%)"
        else:            return pct, f"➖횡보({round(pct,1)}%)"
    except Exception:
        return 0, "➖"

def calc_consecutive_candles(df_final, n=5):
    """연속 양봉/음봉 감지"""
    try:
        closes = df_final['종가'].iloc[-n:]
        opens  = df_final['시가'].iloc[-n:] if '시가' in df_final.columns else None
        if opens is not None:
            candles = [(c > o) for c, o in zip(closes, opens)]
        else:
            candles = [(closes.iloc[i] > closes.iloc[i-1]) for i in range(1, len(closes))]
        if not candles:
            return 0, "➖", 0
        last_dir = candles[-1]
        count = 1
        for c in reversed(candles[:-1]):
            if c == last_dir:
                count += 1
            else:
                break
        signed = count if last_dir else -count
        if last_dir:
            if count >= 5: disp, sc = f"🔴연속양봉{count}개", -1
            elif count >= 3: disp, sc = f"📈양봉{count}개", 1
            else:            disp, sc = f"📈양봉{count}개", 0
        else:
            if count >= 5: disp, sc = f"🔵연속음봉{count}개", 1
            elif count >= 3: disp, sc = f"📉음봉{count}개", -1
            else:            disp, sc = f"📉음봉{count}개", 0
        return signed, disp, sc
    except Exception:
        return 0, "➖", 0

def calc_volume_with_direction(df_final):
    """거래량 + 방향성 통합 판단"""
    try:
        last     = df_final.iloc[-1]
        prev     = df_final.iloc[-2]
        vol_r    = last['vol_ratio'] if not pd.isna(last['vol_ratio']) else 1.0
        up_day = last['종가'] > prev['종가']
        if vol_r >= 2.0:
            if up_day:
                disp, sc = f"{vol_r:.1f}배^ 📈급등", 1
            else:
                disp, sc = f"{vol_r:.1f}배^ 📉급락", -1
        elif vol_r >= 1.5:
            disp, sc = f"{vol_r:.1f}배^ {'📈' if up_day else '📉'}", 0
        elif vol_r < 0.5:
            disp, sc = f"{vol_r:.1f}배v ➖거래고갈", -1
        else:
            disp, sc = f"{vol_r:.1f}배", 0
        return vol_r, disp, sc, up_day
    except Exception:
        return 1.0, "➖", 0, True

def calc_trading_amount(df_final, min_amount_bil=30):
    """거래대금 필터"""
    try:
        last5 = df_final.iloc[-5:]
        amounts = last5['종가'] * last5['거래량'] / 1e8
        avg_amount = amounts.mean()
        if avg_amount >= 500:   disp = f"{avg_amount:,.0f}억 🔴대형"
        elif avg_amount >= 100: disp = f"{avg_amount:,.0f}억 🟠중형"
        elif avg_amount >= min_amount_bil: disp = f"{avg_amount:,.0f}억 🟡소형"
        else:                   disp = f"{avg_amount:,.0f}억 ⚠️소형"
        is_enough = avg_amount >= min_amount_bil
        return avg_amount, disp, is_enough
    except Exception:
        return 0, "-", False

# ---------------------------------------------
# 지표 계산
# ---------------------------------------------

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

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

def get_bb_squeeze_status(bandwidth_series):
    recent = bandwidth_series.iloc[-20:]
    cur = bandwidth_series.iloc[-1]
    p20 = recent.quantile(0.20)
    p80 = recent.quantile(0.80)
    if cur <= p20:
        return "⚡ 수축(폭발 대기)", True
    elif cur >= p80:
        return "💥 팽창(추세 진행)", False
    else:
        return "➖ 보통", False

# ---------------------------------------------
# 매수 타이밍 판단
# ---------------------------------------------

def detect_20ma_touch(df_final):
    try:
        last  = df_final.iloc[-1]
        prev  = df_final.iloc[-2]
        price = last["종가"]
        ma20  = last["20MA"]
        disparity = ((price - ma20) / ma20 * 100) if ma20 > 0 else 0
        prev_disp = ((prev["종가"] - prev["20MA"]) / prev["20MA"] * 100) if prev["20MA"] > 0 else 0
        if prev_disp < 0 and disparity >= 0:
            return True, 0, "🎯20MA골든터치"
        if -3 <= disparity <= 3 and price >= prev["종가"]:
            return True, 0, f"🎯20MA근접({disparity:+.1f}%)"
        for d in range(1, 6):
            r = df_final.iloc[-(d+1)]
            r_disp = ((r["종가"] - r["20MA"]) / r["20MA"] * 100) if r["20MA"] > 0 else 0
            if -3 <= r_disp <= 3 and price > r["종가"]:
                return True, d, f"🎯20MA터치후{d}일"
        return False, None, f"이격{disparity:+.1f}%"
    except Exception:
        return False, None, "-"

def detect_macd_turn(df_final, lookback=5):
    try:
        cur_hist = df_final["MACD_hist"].iloc[-1]
        if cur_hist <= 0:
            slope = df_final["MACD_hist"].iloc[-1] - df_final["MACD_hist"].iloc[-3]
            if slope > 0:
                return False, None, "📊MACD회복중"
            return False, None, f"📊MACD음({cur_hist:.0f})"
        for d in range(1, lookback + 1):
            prev_hist = df_final["MACD_hist"].iloc[-(d+1)]
            if prev_hist <= 0:
                return True, d - 1, f"📊MACD전환{d-1}일전" if d > 1 else "📊MACD골든전환"
        return True, lookback, f"📊MACD양수유지"
    except Exception:
        return False, None, "-"

def detect_cci_turn(df_final, lookback=5):
    try:
        cur_cci = df_final["CCI"].iloc[-1]
        if cur_cci > 0:
            for d in range(1, lookback + 1):
                prev_cci = df_final["CCI"].iloc[-(d+1)]
                if prev_cci <= 0:
                    label = "CCI제로돌파" if d == 1 else f"CCI전환{d-1}일"
                    return True, d - 1, f"📊{label}"
            return True, lookback, "📊CCI양수유지"
        if cur_cci > -100:
            prev_cci = df_final["CCI"].iloc[-2]
            if prev_cci < -100:
                return True, 0, "📊CCI과매도탈출"
        slope = cur_cci - df_final["CCI"].iloc[-3]
        if slope > 0:
            return False, None, f"📊CCI회복중({cur_cci:.0f})"
        return False, None, f"📊CCI음({cur_cci:.0f})"
    except Exception:
        return False, None, "-"

def calc_buy_signal(last, prev, df_final,
                    ichimoku_status, rsi_val,
                    amount_ok, foreign_ratio,
                    vol_dir_score, consec_score,
                    disparity):
    above_cloud  = "구름대 위"  in ichimoku_status or "상향돌파" in ichimoku_status
    below_cloud  = "구름대 아래" in ichimoku_status or "하향이탈" in ichimoku_status
    fall_entry   = "하락진입"   in ichimoku_status
    inside_cloud = "내부"       in ichimoku_status
    price    = last["종가"]
    ma60     = last["60MA"]
    above_60 = price > ma60 if not pd.isna(ma60) else False
    rsi_hot     = rsi_val >= 70
    high_disp   = disparity > 15
    ma20_touch, _, ma20_disp = detect_20ma_touch(df_final)
    macd_turn,  _, macd_disp  = detect_macd_turn(df_final)
    cci_turn,   _, cci_disp   = detect_cci_turn(df_final)
    if fall_entry:
        signal, tag = "⚠️ 구름대주의", "caution"
    elif below_cloud:
        hist_now, hist_prev = last["MACD_hist"], prev["MACD_hist"]
        cci_now, cci_prev = last["CCI"], prev["CCI"]
        macd_dead = hist_now < 0 and hist_prev >= 0
        cci_dead  = cci_now < 0 and cci_prev >= 0
        if macd_dead and cci_dead: signal = "🧊 적극매도"
        elif "이탈" in ichimoku_status: signal = "📉 매도관심"
        elif macd_turn or cci_turn: signal = "🔄 바닥탐색"
        else: signal = "🔽 추세하락"
        tag = "sell"
    elif inside_cloud:
        signal, tag = "🌫️ 구름대내부", "neutral"
    elif above_cloud:
        stage1 = above_60
        if not stage1:
            signal, tag = "🛡️ 홀딩", "hold"
        elif rsi_hot or high_disp:
            signal, tag = "🔼 추세상승(과열)", "hold"
        else:
            turn_count = sum([ma20_touch, macd_turn, cci_turn])
            if turn_count >= 3:
                signal, tag = "🎯 매수타이밍", "buy_strong"
            elif turn_count == 2:
                signal, tag = "📈 매수준비", "buy"
            elif turn_count == 1:
                signal, tag = "🔔 관찰등록", "watch"
            else:
                signal, tag = "🛡️ 홀딩", "hold"
        if rsi_hot: signal += "(RSI과열)"
        elif high_disp and "타이밍" in signal: signal = signal.replace("🎯 매수타이밍", "🎯 매수타이밍(고이격)")
    else:
        signal, tag = "⏸️ 관망", "neutral"
    if not amount_ok and tag in ("buy_strong", "buy", "watch"):
        signal = f"⚠️소형 {signal}"
    return signal, tag, ma20_disp, macd_disp, cci_disp

# ---------------------------------------------
# 종목 분석 메인
# ---------------------------------------------

def analyze_stock(code, name, current_change, foreign_dict=None, fetch_investor=True):
    try:
        df_price = get_price_data(code, max_pages=25)
        if df_price is None or len(df_price) < 80:
            return None
        df = df_price.set_index('날짜').copy()
        df['5MA'] = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['60MA'] = df['종가'].rolling(60).mean()
        high_9, low_9 = df['고가'].rolling(9).max(), df['저가'].rolling(9).min()
        df['tenkan_sen'] = (high_9 + low_9) / 2
        high_26, low_26 = df['고가'].rolling(26).max(), df['저가'].rolling(26).min()
        df['kijun_sen'] = (high_26 + low_26) / 2
        high_52, low_52 = df['고가'].rolling(52).max(), df['저가'].rolling(52).min()
        df['senkou_b_base'] = (high_52 + low_52) / 2
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_Signal']
        df['RSI'] = calc_rsi(df['종가'])
        df['CCI'] = calc_cci(df)
        df['BB_upper'], df['BB_lower'], df['BB_width'] = calc_bollinger(df['종가'])
        df['vol_ratio'] = df['거래량'] / df['거래량'].rolling(20).mean()
        df_future = pd.DataFrame(index=df.index)
        df_future['senkou_a'] = (df['tenkan_sen'] + df['kijun_sen']) / 2
        df_future['senkou_b'] = df['senkou_b_base']
        df_future = df_future.shift(26)
        df_merged = pd.merge(df, df_future, left_index=True, right_index=True, how='left')
        df_final  = df_merged.dropna(subset=['senkou_a', 'senkou_b', 'RSI', 'BB_width', 'CCI']).copy()
        if len(df_final) < 6: return None
        last, prev, prev2, prev3, prev4 = df_final.iloc[-1], df_final.iloc[-2], df_final.iloc[-3], df_final.iloc[-4], df_final.iloc[-5]
        price_col = '종가'

        def cloud_top(row): return max(row['senkou_a'], row['senkou_b'])
        def cloud_bot(row): return min(row['senkou_a'], row['senkou_b'])
        price_now, ct_now, cb_now = last['종가'], cloud_top(last), cloud_bot(last)
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
        if above_now:
            if breakout_days is not None: ichimoku_status = f"🔥 상향돌파({breakout_days}일전)"
            else: ichimoku_status = "📈 구름대 위"
        elif below_now:
            if breakdown_days is not None: ichimoku_status = f"🧊 하향이탈({breakdown_days}일전)"
            else: ichimoku_status = "📉 구름대 아래"
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
            return "📈^" if l[price_col] > l[ma_col] else "📉v"
        ma_text = f"5:{ma_cross(last,prev,'5MA')} 20:{ma_cross(last,prev,'20MA')} 60:{ma_cross(last,prev,'60MA')}"

        cci_now, cci_prev, cci_val = last['CCI'], prev['CCI'], round(last['CCI'], 1)
        if cci_prev < -100 and cci_now >= -100: cci_display = f"{cci_val} 🟢과매도탈출"
        elif cci_prev < 0 and cci_now >= 0: cci_display = f"{cci_val} 🔵제로크로스"
        elif cci_prev > 100 and cci_now <= 100: cci_display = f"{cci_val} 🟡과매수탈출"
        elif cci_prev > 0 and cci_now <= 0: cci_display = f"{cci_val} 🔴제로데드"
        elif cci_now > 100: cci_display = f"{cci_val} ⚡과매수"
        elif cci_now < -100: cci_display = f"{cci_val} 💧과매도"
        else: cci_display = f"{cci_val} ➖중립"

        bb_status, _ = get_bb_squeeze_status(df_final['BB_width'])
        if last['종가'] >= last['BB_upper']: bb_pos = "상단"
        elif last['종가'] <= last['BB_lower']: bb_pos = "하단"
        else: bb_pos = "내부"
        bb_display = f"{bb_status}/{bb_pos}"

        _, vol_display, vol_dir_score, _ = calc_volume_with_direction(df_final)
        _, consec_display, consec_score = calc_consecutive_candles(df_final)
        _, amount_display, amount_ok = calc_trading_amount(df_final)
        disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        _, slope_display = get_ma5_slope(df['종가'])
        
        foreign_ratio = 0.0
        if fetch_investor and foreign_dict is not None:
            foreign_ratio = foreign_dict.get(code, 0.0)
        investor_display = _fmt_ratio(foreign_ratio) if foreign_ratio > 0 else "-"

        signal, _, ma20_disp, macd_disp, cci_disp_timing = calc_buy_signal(
            last, prev, df_final, ichimoku_status, round(last['RSI'], 1),
            amount_ok, foreign_ratio, vol_dir_score, consec_score, disparity
        )
        timing_display = f"{ma20_disp} | {macd_disp} | {cci_disp_timing}"
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [
            code, name, current_change, int(last['종가']), disparity_fmt,
            signal, ichimoku_status, ma_text, cci_display, bb_display,
            vol_display, consec_display, amount_display, investor_display,
            slope_display, timing_display, chart_url
        ]
    except Exception as e:
        # st.error(f"Error analyzing {name}: {e}") # Optional: for debugging
        return None


# ---------------------------------------------
# 스타일 데이터프레임 표시
# ---------------------------------------------

COLUMNS = ['코드', '종목명', '등락률', '현재가', '이격률', '신호', '일목(일봉)',
           'MA크로스', 'CCI', 'BB상태', '거래량', '연속봉', '거래대금',
           '외국인지분율', '5MA기울기', '타이밍상태', '차트']

def style_signal(val):
    v = str(val)
    if '매수타이밍' in v: return 'color:white;background-color:#b71c1c;font-weight:bold'
    if '매수준비'   in v: return 'color:#ef5350;font-weight:bold'
    if '관찰등록'   in v: return 'color:#ff8f00;font-weight:bold'
    if '홀딩'       in v: return 'color:#2e7d32;font-weight:bold'
    if '추세상승'   in v: return 'color:#558b2f'
    if '구름대내부' in v: return 'color:#78909c'
    if '구름대주의' in v: return 'color:white;background-color:#e65100;font-weight:bold'
    if '적극매도'   in v: return 'color:white;background-color:#0d47a1;font-weight:bold'
    if '매도관심'   in v: return 'color:#42a5f5;font-weight:bold'
    if '추세하락'   in v: return 'color:#1565c0;font-weight:bold'
    if '바닥탐색'   in v: return 'color:#8d6e63'
    return 'color:#9e9e9e' # 관망

def style_ichimoku(val):
    v = str(val)
    if '상향돌파'   in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '하향이탈'   in v: return 'color:white;background-color:#1565c0;font-weight:bold'
    if '하락진입'   in v: return 'color:white;background-color:#e65100;font-weight:bold'
    if '상승진입'   in v: return 'color:#ff8f00;font-weight:bold'
    if '구름대 위'  in v: return 'color:#ef5350'
    if '구름대 아래'in v: return 'color:#64b5f6'
    return 'color:#9e9e9e'

def style_cci(val):
    v = str(val)
    if '과매도탈출' in v: return 'color:#43a047;font-weight:bold'
    if '제로크로스' in v and '🔵' in v: return 'color:#1e88e5;font-weight:bold'
    if '제로데드'   in v: return 'color:#e53935;font-weight:bold'
    if '과매수탈출' in v: return 'color:#fb8c00;font-weight:bold'
    if '과매수'     in v: return 'color:#e53935'
    if '과매도'     in v: return 'color:#43a047'
    return ''

def style_pct(val):
    v = str(val).strip()
    if not v or v == '-': return ''
    try:
        num = float(v.replace('%', '').replace(',', ''))
        if num > 0: return 'color:#ef5350'
        if num < 0: return 'color:#42a5f5'
    except Exception: pass
    return ''

def style_timing(val):
    v = str(val)
    if '골든터치' in v or '골든전환' in v: return 'color:#b71c1c;font-weight:bold'
    if '20MA근접' in v or '터치후' in v: return 'color:#ef5350'
    if 'MACD전환' in v or 'CCI돌파' in v: return 'color:#ff8f00'
    if '회복중' in v: return 'color:#f9a825'
    return 'color:#9e9e9e'

def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다.")
        return

    dynamic_height = (len(dataframe) + 1) * 35 + 3
    
    # Define styles for columns that might not exist in every run
    # to avoid KeyError on subset
    styling_map = {
        '신호': style_signal,
        '일목(일봉)': style_ichimoku,
        'CCI': style_cci,
        '등락률': style_pct,
        '이격률': style_pct,
        '타이밍상태': style_timing,
    }
    
    styled = dataframe.style
    for col, style_func in styling_map.items():
        if col in dataframe.columns:
            styled = styled.map(style_func, subset=[col])

    # Lambda styles
    if 'MA크로스' in dataframe.columns:
        styled = styled.map(lambda x: ('color:#b71c1c;font-weight:bold' if '🔥' in str(x) else
                                      'color:#0d47a1;font-weight:bold' if '🧊' in str(x) else
                                      'color:#ef5350' if '📈' in str(x) else
                                      'color:#42a5f5' if '📉' in str(x) else ''),
                            subset=['MA크로스'])
    if 'BB상태' in dataframe.columns:
        styled = styled.map(lambda x: ('color:#ef9a00;font-weight:bold' if '⚡' in str(x) else
                                      'color:#26a69a;font-weight:bold' if '💥' in str(x) else
                                      'color:#ef5350' if '상단' in str(x) else
                                      'color:#42a5f5' if '하단' in str(x) else ''),
                            subset=['BB상태'])
    if '거래량' in dataframe.columns:
        styled = styled.map(lambda x: ('color:#ef5350' if '📈' in str(x) else
                                      'color:#64b5f6' if '📉' in str(x) else ''),
                            subset=['거래량'])

    col_cfg = {
        "코드": st.column_config.TextColumn("코드"),
        "등락률": st.column_config.TextColumn("등락"),
        "이격률": st.column_config.TextColumn("이격"),
        "거래량": st.column_config.TextColumn("거래량"),
        "연속봉": st.column_config.TextColumn("연속봉"),
        "거래대금": st.column_config.TextColumn("거래대금"),
        "차트": st.column_config.LinkColumn("차트", display_text="📊"),
        "신호": st.column_config.TextColumn("신호"),
        "일목(일봉)": st.column_config.TextColumn("일목"),
        "MA크로스": st.column_config.TextColumn("MA"),
        "CCI": st.column_config.TextColumn("CCI"),
        "BB상태": st.column_config.TextColumn("BB"),
        "종목명": st.column_config.TextColumn("종목명"),
        "현재가": st.column_config.NumberColumn("현재가"),
        "외국인지분율":st.column_config.TextColumn("외국인%"),
        "5MA기울기": st.column_config.TextColumn("5MA"),
        "타이밍상태": st.column_config.TextColumn("20MA|MACD|CCI"),
    }
    st.dataframe(
        styled,
        use_container_width=True,
        height=dynamic_height,
        column_config=col_cfg,
        hide_index=True
    )
# ---------------------------------------------
# UI
# ---------------------------------------------

st.set_page_config(layout="wide")
st.title("🛡️ 스마트 데이터 스캐너 v5")

with st.sidebar:
    st.header("설정")
    market = st.radio("시장 선택", ["KOSPI", "KOSDAQ"])
    selected_pages = st.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
    st.markdown("---")
    use_investor = st.checkbox("📡 외국인 지분율 수집", value=True, help="분석 시작 전 전체 수집 (약 20~30초 추가)")
    st.markdown("---")
    st.markdown("""
    **🎯 6단계 신호 기준**
    | 신호 | 조건 |
    |---|---|
    | 🎯 매수타이밍 | 구름대위+60MA위+20MA터치+MACD전환+CCI전환 |
    | 📈 매수준비 | 위 3가지 중 2가지 충족 |
    | 🔔 관찰등록 | 위 3가지 중 1가지 충족 |
    | 🛡️ 홀딩 | 구름대 위, 신호 없음 |
    | ⚠️ 구름대주의 | 위에서 하락 진입 중 |
    | 📉 매도/하락 | 구름대 아래 |
    
    **핵심 3가지 (AND 조건)**
    - 🎯 20MA 터치/반등
    - 📊 MACD 음→양 전환
    - 📊 CCI 음→양 전환
    
    **보조 필터**
    - RSI 70 이상 → 과열 경고 부기
    - 이격률 15% 초과 → 추세상승(과열)
    - 거래대금 30억 미만 → ⚠️소형 표기
    """)
    start_btn = st.button("🚀 분석 시작")


st.subheader("📊 진단 및 필터링")
c1, c2, c3, c4, c5, c6 = st.columns(6)
total_metric   = c1.metric("전체", "0개")
timing_metric  = c2.metric("🎯매수타이밍", "0개")
ready_metric   = c3.metric("📈매수준비", "0개")
watch_metric   = c4.metric("🔔관찰등록", "0개")
caution_metric = c5.metric("⚠️구름주의", "0개")
sell_metric    = c6.metric("📉매도/하락", "0개")

if 'filter' not in st.session_state:
    st.session_state.filter = "전체"

filter_cols = st.columns(7)
if filter_cols[0].button("🔄전체", use_container_width=True): st.session_state.filter = "전체"
if filter_cols[1].button("🎯매수타이밍", use_container_width=True): st.session_state.filter = "타이밍"
if filter_cols[2].button("📈매수준비", use_container_width=True): st.session_state.filter = "준비"
if filter_cols[3].button("🔔관찰등록", use_container_width=True): st.session_state.filter = "관찰"
if filter_cols[4].button("🛡️홀딩", use_container_width=True): st.session_state.filter = "홀딩"
if filter_cols[5].button("⚠️구름주의", use_container_width=True): st.session_state.filter = "구름대주의"
if filter_cols[6].button("📉매도하락", use_container_width=True): st.session_state.filter = "매도"

st.markdown("---")
result_title = st.empty()
main_result_area = st.container()

def update_metrics_display(df):
    c1.metric("전체", f"{len(df)}개")
    c2.metric("🎯매수타이밍", f"{len(df[df['신호'].str.contains('매수타이밍', regex=False)])}개")
    c3.metric("📈매수준비", f"{len(df[df['신호'].str.contains('매수준비', regex=False)])}개")
    c4.metric("🔔관찰등록", f"{len(df[df['신호'].str.contains('관찰등록', regex=False)])}개")
    c5.metric("⚠️구름주의", f"{len(df[df['신호'].str.contains('구름대주의', regex=False)])}개")
    c6.metric("📉매도/하락", f"{len(df[df['신호'].str.contains('매도|하락|매도관심|적극매도', regex=True)])}개")

def apply_filter(df, f):
    if f == "타이밍": return df[df['신호'].str.contains("매수타이밍", regex=False)]
    if f == "준비": return df[df['신호'].str.contains("매수준비", regex=False)]
    if f == "관찰": return df[df['신호'].str.contains("관찰등록", regex=False)]
    if f == "홀딩": return df[df['신호'].str.contains("홀딩", regex=False)]
    if f == "구름대주의": return df[df['신호'].str.contains("구름대주의", regex=False)]
    if f == "매도": return df[df['신호'].str.contains("매도|하락", regex=True)]
    return df

if start_btn:
    st.session_state.filter = "전체"
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        st.session_state['df_all'] = pd.DataFrame()
        foreign_dict = {}
        if use_investor:
            with st.spinner(f"📡 {market} 외국인 지분율 수집 중..."):
                foreign_dict = load_foreign_ratio_all(market=market, max_pages=40)
            st.info(f"✅ {len(foreign_dict):,}개 종목 외국인 지분율 수집 완료")
        
        progress_bar = st.progress(0, text="분석 시작...")
        total_stocks = len(market_df)
        for i, (_, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'],
                                foreign_dict=foreign_dict, fetch_investor=use_investor)
            if res:
                results.append(res)
            
            # Update UI periodically to avoid overwhelming the browser
            if (i + 1) % 10 == 0 or (i + 1) == total_stocks:
                if results:
                    df_all = pd.DataFrame(results, columns=COLUMNS)
                    sig_order = {"🎯 매수타이밍": 0, "📈 매수준비": 1, "🔔 관찰등록": 2, "🛡️ 홀딩": 3, 
                                 "⏸️ 관망": 4, "🌫️ 구름대내부": 5, "⚠️ 구름대주의": 6, "🔄 바닥탐색": 7, 
                                 "🔽 추세하락": 8, "📉 매도관심": 9, "🧊 적극매도": 10}
                    df_all['_ord'] = df_all['신호'].apply(lambda s: next((v for k, v in sig_order.items() if k in s), 99))
                    df_all = df_all.sort_values('_ord').drop(columns='_ord').reset_index(drop=True)
                    st.session_state['df_all'] = df_all
                    
                    update_metrics_display(df_all)
                    display_df = apply_filter(df_all, st.session_state.filter)
                    result_title.subheader(f"🔍 결과 ({st.session_state.filter} / {len(display_df)}개)")
                    with main_result_area:
                        show_styled_dataframe(display_df)

            progress_bar.progress((i + 1) / total_stocks, text=f"분석 중: {row['종목명']} ({i+1}/{total_stocks})")
            
        progress_bar.empty()
        st.success("✅ 분석 완료!")

if not start_btn and 'df_all' in st.session_state and not st.session_state['df_all'].empty:
    df = st.session_state['df_all']
    display_df = apply_filter(df, st.session_state.filter)
    update_metrics_display(df)
    result_title.subheader(f"🔍 결과 ({st.session_state.filter} / {len(display_df)}개)")
    with main_result_area:
        show_styled_dataframe(display_df)
    
    if not display_df.empty:
        email_summary = display_df[['종목명', '현재가', '신호', '일목(일봉)', '타이밍상태']].to_string(index=False)
        encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
        mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
        st.markdown(
            f'<a href="{mailto_url}" target="_self" style="text-decoration:none;">'
            f'<div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>',
            unsafe_allow_html=True
        )
elif 'df_all' not in st.session_state or st.session_state['df_all'].empty:
    with main_result_area:
        st.info("왼쪽 사이드바에서 '분석 시작' 버튼을 눌러주세요.")
