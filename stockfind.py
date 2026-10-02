import streamlit as st
import requests
import pandas as pd
import numpy as np
import time
import io
import urllib.parse
import urllib3
from datetime import datetime

# Bosch 사내망 환경 경고 차단
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────
# 1. 네이버 개편 대응 API 헬퍼 함수
# ─────────────────────────────────────────────
def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://stock.naver.com/'
    }

def get_market_sum_pages(page_list, market="KOSPI"):
    """
    m.stock.naver.com API로 KOSPI/KOSDAQ 전체 종목 수집
    pageSize=100 기준, page_list=[1,2,3...] 으로 페이지 지정
    """
    all_stocks = []
    market_type = "KOSPI" if market == "KOSPI" else "KOSDAQ"

    for page in page_list:
        url = (f"https://m.stock.naver.com/api/stocks/marketValue/{market_type}"
               f"?page={page}&pageSize=100")
        try:
            res = requests.get(url, headers=get_headers(),
                               timeout=10, verify=False)
            res.raise_for_status()
            data = res.json()

            # 실제 응답 구조: {"stocks": [...], "totalCount": N}
            stocks_list = (data.get('stocks') or
                           data.get('items') or
                           data.get('stockList') or
                           (data if isinstance(data, list) else []))

            for item in stocks_list:
                code      = item.get('itemCode') or item.get('itemcode')
                name      = item.get('stockName') or item.get('itemname')
                price_val = item.get('closePrice') or item.get('nowPrice', 0)
                flu_ratio = item.get('fluctuationsRatio') or item.get('prevChangeRate', 0.0)

                fl_val    = float(flu_ratio) if flu_ratio is not None else 0.0
                fl_prefix = "+" if fl_val > 0 else ""
                flu_str   = f"{fl_prefix}{round(fl_val, 2)}%"

                if code and name:
                    all_stocks.append({
                        '종목코드': str(code).zfill(6),
                        '종목명':   name,
                        '등락률':   flu_str,
                        '현재가':   int(str(price_val).replace(',', ''))
                                   if price_val else 0
                    })

            time.sleep(0.1)

        except Exception:
            continue

    if not all_stocks:
        return pd.DataFrame(columns=['종목코드', '종목명', '등락률', '현재가'])
    return pd.DataFrame(all_stocks)

def get_price_data(code, max_pages=10, page_size=60):
    """
    [검증 완료] 신규 비동기 일별 시세 API를 사용하여
    단 10번의 호출로 600일 치 가격 데이터를 고속으로 긁어옵니다.
    """
    dfs = []
    for page in range(1, max_pages + 1):
        url = f"https://m.stock.naver.com/api/stock/{code}/price?pageSize={page_size}&page={page}"
        try:
            res = requests.get(url, headers=get_headers(), timeout=10, verify=False)
            if res.status_code != 200:
                break
            data = res.json()
            if not data or not isinstance(data, list) or len(data) == 0:
                break
            dfs.append(pd.DataFrame(data))
            time.sleep(0.05)
        except Exception as e:
            break

    if not dfs:
        return pd.DataFrame()

    full_df = pd.concat(dfs, ignore_index=True)

    # 기존 계산 로직 필드명과 호환성 매칭
    full_df = full_df.rename(columns={
        'localTradedAt': '날짜',
        'closePrice': '종가',
        'openPrice': '시가',
        'highPrice': '고가',
        'lowPrice': '저가',
        'accumulatedTradingVolume': '거래량'
    })

    full_df['날짜'] = pd.to_datetime(full_df['날짜'], errors='coerce')
    for col in ['종가', '시가', '고가', '저가', '거래량']:
        if col in full_df.columns:
            full_df[col] = pd.to_numeric(full_df[col].astype(str).str.replace(',', ''), errors='coerce')

    return full_df.dropna(subset=['날짜', '종가']).sort_values('날짜').reset_index(drop=True)

# ─────────────────────────────────────────────
# 2. 보존된 보조 지표 연산 및 점수 산정 엔진
# ─────────────────────────────────────────────
def get_ma5_slope(price_series):
    try:
        ma5 = price_series.rolling(5).mean()
        if len(ma5) < 4:
            return 0
        slope = ma5.iloc[-1] - ma5.iloc[-3]
        pct = slope / ma5.iloc[-3] * 100 if ma5.iloc[-3] != 0 else 0
        return pct
    except Exception:
        return 0

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

def get_market_trend(market):
    index_code = "0001" if market == "KOSPI" else "1001"
    index_name = "코스피" if market == "KOSPI" else "코스닥"
    df = get_price_data(index_code, max_pages=3, page_size=60)
    if df is None or len(df) < 60:
        return f"{index_name} 추세: 데이터 부족"

    close = df['종가']
    current = close.iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    change_20 = (current / close.iloc[-21] - 1) * 100

    if current > ma20 and ma20 > ma60:
        status = "상승 추세"
    elif current < ma20 and ma20 < ma60:
        status = "하락 추세"
    else:
        status = "혼조/횡보"
    return f"{index_name} 추세: {status} | 현재가 {current:,.0f} | 20일 {change_20:+.1f}%"

def calc_accumulation(last):
    """20일 누적거래량 급증 + 가격 횡보 = 매집, 거래량 급증 + 하락 = 분산으로 판정."""
    surge = last['vol_surge20']
    chg = last['chg20'] * 100 if pd.notna(last['chg20']) else 0.0
    box = last['box20'] * 100 if pd.notna(last['box20']) else 999.0

    if pd.isna(surge):
        return 0, "-"

    if surge >= 1.5 and chg <= -7:
        return -1, f"🚨분산 {surge:.1f}배 {chg:+.1f}%"
    if surge >= 1.8 and -3 <= chg <= 5 and box <= 20:
        return 2, f"🎯강한매집 {surge:.1f}배 {chg:+.1f}%"
    if surge >= 1.3 and -5 <= chg <= 7 and box <= 25:
        return 1, f"🔍매집의심 {surge:.1f}배 {chg:+.1f}%"
    return 0, f"{surge:.1f}배 {chg:+.1f}%"

def calc_signal_score(last, prev, ichimoku_status, w_ichimoku_status,
                      cci_now, cci_prev, weekly_two_bullish=False,
                      ma20_breakout=False, dual_cloud_breakout=False,
                      accum_level=0):
    score = 0
    detail = {}

    # 1. 일목 점수 (일봉)
    if '상향돌파' in ichimoku_status: s_ichi = 3
    elif '하향이탈' in ichimoku_status: s_ichi = -3
    elif '상승진입' in ichimoku_status: s_ichi = 1
    elif '하락진입' in ichimoku_status: s_ichi = -2
    else: s_ichi = 0
    score += s_ichi
    detail['구름대(일)'] = s_ichi

    # 2. 일목 점수 (주봉)
    if '상향돌파' in w_ichimoku_status: s_w_ichi = 4
    elif '하향이탈' in w_ichimoku_status: s_w_ichi = -4
    elif '구름대 위' in w_ichimoku_status: s_w_ichi = 2
    elif '구름대 아래' in w_ichimoku_status: s_w_ichi = -2
    elif '상승진입' in w_ichimoku_status: s_w_ichi = 1
    elif '하락진입' in w_ichimoku_status: s_w_ichi = -2
    else: s_w_ichi = 0
    score += s_w_ichi
    detail['구름대(주)'] = s_w_ichi

    # 3. MACD
    hist_now = last['MACD_hist']
    hist_prev = prev['MACD_hist']
    macd_slope = hist_now - hist_prev
    if hist_now > 0 and hist_prev <= 0: s_macd = 2
    elif hist_now < 0 and hist_prev >= 0: s_macd = -2
    elif hist_now < 0 and macd_slope > 0: s_macd = 1
    elif hist_now > 0 and macd_slope < 0: s_macd = -1
    else: s_macd = 0

    # 4. CCI
    if cci_prev < -100 and cci_now >= -100: s_cci = 2
    elif cci_prev < 0 and cci_now >= 0: s_cci = 1
    elif cci_prev > 0 and cci_now <= 0: s_cci = -1
    elif cci_prev > 100 and cci_now <= 100: s_cci = -2
    else: s_cci = 0

    # MACD를 주 모멘텀으로 사용하고, MACD가 중립일 때만 CCI를 보조로 사용
    s_momentum = s_macd if s_macd != 0 else s_cci
    score += s_momentum
    detail['모멘텀'] = s_momentum

    is_above_cloud = '구름대 위' in ichimoku_status or '상향돌파' in ichimoku_status or '구름대 위' in w_ichimoku_status or '상향돌파' in w_ichimoku_status
    is_below_cloud = '구름대 아래' in ichimoku_status or '하향이탈' in ichimoku_status or '구름대 아래' in w_ichimoku_status or '하향이탈' in w_ichimoku_status
    is_falling_entry = '하락진입' in ichimoku_status or '구름대하락진입' in ichimoku_status or '구름대하락진입' in w_ichimoku_status

    cloud_breakout = '상향돌파' in ichimoku_status or '상향돌파' in w_ichimoku_status
    cloud_breakdown = '하향이탈' in ichimoku_status or '하향이탈' in w_ichimoku_status
    momentum_up = detail['모멘텀'] >= 1
    momentum_down = detail['모멘텀'] <= -1
    has_turn = cloud_breakout or cloud_breakdown or momentum_up or momentum_down

    vol_ratio = last['vol_ratio'] if not pd.isna(last['vol_ratio']) else 1.0
    down_volume_surge = last['종가'] < prev['종가'] and vol_ratio >= 2.0
    low_volume_breakout = cloud_breakout and vol_ratio < 1.0
    if down_volume_surge:
        score -= 2
        detail['거래량위험'] = -2
    else:
        detail['거래량위험'] = 0

    # 주봉 추세 확인과 20일선 돌파는 일목 신호의 신뢰도를 보강한다.
    if weekly_two_bullish:
        score += 1
    if ma20_breakout:
        score += 1
    detail['2주양봉'] = 1 if weekly_two_bullish else 0
    detail['20일선돌파'] = 1 if ma20_breakout else 0

    s_accum = {2: 2, 1: 1, -1: -2}.get(accum_level, 0)
    score += s_accum
    detail['매집'] = s_accum
    is_accumulating = accum_level >= 1
    is_distributing = accum_level == -1
    accum_breakout = accum_level == 2 and (cloud_breakout or ma20_breakout)

    disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
    is_high_disp = disparity > 15
    is_low_disp = disparity < -10
    is_weekly_breakout = '상향돌파' in w_ichimoku_status

    if down_volume_surge: signal = "🚨 하락거래량급증"
    elif is_distributing and momentum_down: signal = "🚨 분산의심"
    elif low_volume_breakout: signal = "⚠️ 거래량없는돌파"
    elif is_falling_entry: signal = "⚠️ 구름대주의"
    elif accum_breakout and momentum_up: signal = "🎯 매집후돌파"
    elif (dual_cloud_breakout and weekly_two_bullish and ma20_breakout
          and vol_ratio >= 1.0): signal = "🔥 양·주봉 동시돌파"
    elif is_weekly_breakout and momentum_up: signal = "🚀 주간돌파!"
    elif (score >= 5 and cloud_breakout and momentum_up): signal = "🔥 적극매수"
    elif (score >= 3 and not is_high_disp and (cloud_breakout or momentum_up)): signal = "📈 매수관심"
    elif (accum_level == 2 and not is_high_disp and score >= 1): signal = "🧺 매집진행"
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

def analyze_stock(code, name, current_change):
    try:
        df_price = get_price_data(code, max_pages=10, page_size=60)
        if df_price is None or len(df_price) < 80:
            return None

        # ─── 1. 일봉 지표 계산 ───
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

        df['CCI'] = calc_cci(df)
        df['vol_ratio'] = df['거래량'] / df['거래량'].rolling(20).mean()

        # 최근 20일 누적거래량을 직전 20일 구간과 비교해 매집/분산을 판별한다.
        vol_sum20 = df['거래량'].rolling(20).sum()
        df['vol_surge20'] = vol_sum20 / vol_sum20.shift(20).replace(0, np.nan)
        df['chg20'] = df['종가'] / df['종가'].shift(20) - 1
        low20 = df['저가'].rolling(20).min()
        df['box20'] = (df['고가'].rolling(20).max() - low20) / low20.replace(0, np.nan)

        true_range = pd.concat([
            df['고가'] - df['저가'],
            (df['고가'] - df['종가'].shift(1)).abs(),
            (df['저가'] - df['종가'].shift(1)).abs()
        ], axis=1).max(axis=1)
        df['ATR14'] = true_range.rolling(14).mean()

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

        # --- 일봉 일목 엄격 돌파 감지 알고리즘 ---
        price_now = last['종가']
        def cloud_top(row): return max(row['senkou_a'], row['senkou_b'])
        def cloud_bot(row): return min(row['senkou_a'], row['senkou_b'])

        ct_now, cb_now = cloud_top(last), cloud_bot(last)
        above_now, below_now = price_now > ct_now, price_now < cb_now

        breakout_days = None
        if above_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] <= cloud_top(row):
                    if price_now > row['종가'] and not (last['종가'] < prev['종가'] < prev2['종가']):
                        breakout_days = days_ago
                        break

        breakdown_days = None
        if below_now:
            for days_ago, row in enumerate([prev, prev2, prev3, prev4], start=1):
                if row['종가'] >= cloud_bot(row):
                    if price_now < row['종가'] and not (last['종가'] > prev['종가'] > prev2['종가']):
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

        # ─── 2. 주봉 지표 및 주봉 일목 구름대 계산 ───
        df_w_final = None
        w_ichimoku_status = "-"
        weekly_two_bullish = False

        df_w = df_price.resample('W', on='날짜').agg({
            '시가': 'first',
            '종가': 'last',
            '고가': 'max',
            '저가': 'min',
            '거래량': 'sum'
        }).dropna()

        if len(df_w) >= 53:
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
                weekly_two_bullish = (
                    w_prev['종가'] > w_prev['시가']
                    and w_prev2['종가'] > w_prev2['시가']
                )

                w_price_now = w_last['종가']
                def w_cloud_top(row): return max(row['senkou_a'], row['senkou_b'])
                def w_cloud_bot(row): return min(row['senkou_a'], row['senkou_b'])

                w_ct_now, w_cb_now = w_cloud_top(w_last), w_cloud_bot(w_last)
                w_above_now = w_price_now > w_ct_now
                w_below_now = w_price_now < w_cb_now

                w_breakout_weeks = None
                if w_above_now:
                    for weeks_ago, row in enumerate([w_prev, w_prev2, w_prev3, w_prev4], start=1):
                        if row['종가'] <= w_cloud_top(row):
                            if w_price_now > row['종가'] and not (w_last['종가'] < w_prev['종가'] < w_prev2['종가']):
                                w_breakout_weeks = weeks_ago
                                break

                w_breakdown_weeks = None
                if w_below_now:
                    for weeks_ago, row in enumerate([w_prev, w_prev2, w_prev3, w_prev4], start=1):
                        if row['종가'] >= w_cloud_bot(row):
                            if w_price_now < row['종가'] and not (w_last['종가'] > w_prev['종가'] > w_prev2['종가']):
                                w_breakdown_weeks = weeks_ago
                                break

                if w_above_now:
                    w_ichimoku_status = f"🔥 상향돌파({w_breakout_weeks}주전)" if w_breakout_weeks is not None else "📈 구름대 위"
                elif w_below_now:
                    w_ichimoku_status = f"🧊 하향이탈({w_breakdown_weeks}주전)" if w_breakdown_weeks is not None else "📉 구름대 아래"
                else:
                    w_prior_rows = [w_prev, w_prev2, w_prev3, w_prev4]
                    w_was_above = any(r['종가'] > w_cloud_top(r) for r in w_prior_rows)
                    w_was_below = any(r['종가'] < w_cloud_bot(r) for r in w_prior_rows)
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

        ma20_breakout = prev['종가'] <= prev['20MA'] and last['종가'] > last['20MA']
        dual_cloud_breakout = (
            '상향돌파' in ichimoku_status
            and '상향돌파' in w_ichimoku_status
        )

        accum_level, accum_display = calc_accumulation(last)

        # --- 점수 및 최종 신호 계산 ---
        score, signal, detail = calc_signal_score(
            last, prev, ichimoku_status, w_ichimoku_status, cci_now, cci_prev,
            weekly_two_bullish, ma20_breakout, dual_cloud_breakout, accum_level
        )
        atr_value = last['ATR14']
        stop_reference = (max(0, int(last['종가'] - 1.5 * atr_value))
                  if pd.notna(atr_value) else 0)

        chart_url = f"https://finance.daum.net/quotes/A{code}#chart"

        return [
            code, name, current_change,
            int(last['종가']), disparity_fmt,
            score, signal,
            ichimoku_status, w_ichimoku_status, ma_text,
            cci_display, vol_display, accum_display, stop_reference,
            chart_url
        ]
    except Exception as e:
        return None

# ─────────────────────────────────────────────
# 3. 스타일링 테마 및 디스플레이 정의
# ─────────────────────────────────────────────
COLUMNS = ['코드', '종목명', '등락률', '현재가', '이격률',
           '총점', '신호',
           '일목(일봉)', '일목(주봉)', 'MA크로스',
           'CCI', '거래량', '매집(20일)', '손절참고',
           '차트']

def style_signal(val):
    v = str(val)
    if '매집후돌파' in v: return 'color:white;background-color:#00695c;font-weight:bold'
    if '매집진행' in v: return 'color:#00897b;font-weight:bold'
    if '분산의심' in v: return 'color:white;background-color:#37474f;font-weight:bold'
    if '양·주봉 동시돌파' in v: return 'color:white;background-color:#8e24aa;font-weight:bold'
    if '주간돌파' in v: return 'color:white;background-color:#d32f2f;font-weight:bold;'
    if '하락거래량급증' in v: return 'color:white;background-color:#6a1b9a;font-weight:bold'
    if '거래량없는돌파' in v: return 'color:#e65100;font-weight:bold'
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

def style_accum(val):
    v = str(val)
    if '강한매집' in v: return 'color:white;background-color:#00695c;font-weight:bold'
    if '매집의심' in v: return 'color:#00897b;font-weight:bold'
    if '분산' in v: return 'color:white;background-color:#37474f;font-weight:bold'
    return 'color:#9e9e9e'

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
        .map(style_accum,    subset=['매집(20일)'])
        .map(style_pct,      subset=['등락률', '이격률'])
        .map(lambda x: ('color:#b71c1c;font-weight:bold' if '🔥' in str(x) else
                        'color:#0d47a1;font-weight:bold' if '🧊' in str(x) else
                        'color:#ef5350' if '📈' in str(x) else
                        'color:#42a5f5' if '📉' in str(x) else ''),
             subset=['MA크로스'])
        .map(lambda x: ('color:#ef5350' if '📈' in str(x) else
                        'color:#64b5f6' if '📉' in str(x) else ''),
             subset=['거래량'])
    )

    col_cfg = {
        "코드": st.column_config.TextColumn("코드"),
        "총점": st.column_config.NumberColumn("점수"),
        "등락률": st.column_config.TextColumn("등락"),
        "이격률": st.column_config.TextColumn("이격"),
        "거래량": st.column_config.TextColumn("거래량"),
        "매집(20일)": st.column_config.TextColumn("매집(20일)", help="최근 20일 누적거래량 ÷ 직전 20일 누적거래량 / 20일 가격변화"),
        "손절참고": st.column_config.NumberColumn("손절 참고"),
        "차트": st.column_config.LinkColumn("차트", display_text="📊"),
        "신호": st.column_config.TextColumn("신호"),
        "일목(일봉)": st.column_config.TextColumn("일목(일)"),
        "일목(주봉)": st.column_config.TextColumn("일목(주)"),
        "MA크로스": st.column_config.TextColumn("MA"),
        "CCI": st.column_config.TextColumn("CCI"),
        "종목명": st.column_config.TextColumn("종목명"),
        "현재가": st.column_config.NumberColumn("현재가"),
    }

    st.dataframe(
        styled,
        use_container_width=True,
        height=dynamic_height,
        column_config=col_cfg,
        hide_index=True
    )

# ─────────────────────────────────────────────
# 4. Streamlit UI 대시보드 레이아웃 및 제어 흐름
# ─────────────────────────────────────────────
st.title("🛡️ 스마트 데이터 스캐너 v4.4 (코스피 200 최적화)")
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택 (페이지당 100개)", options=list(range(1, 3)), default=[1])
st.sidebar.markdown("---")
st.sidebar.markdown("""
**📊 13단계 신호 기준**

**[매수 계열]**
| 신호 | 의미 |
|:---|:---|
| 🎯 매집후돌파 | 20일 누적거래량 급증 횡보 후 돌파 |
| 🧺 매집진행 | 거래량만 늘고 가격은 제자리 (대기) |
| 🚀 주간돌파! | 주봉 구름대 돌파 + 상승 모멘텀 |
| 🔥 양·주봉 동시돌파 | 일·주봉 구름대 돌파 + 2주 양봉 + 20일선 돌파 |
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
| 🚨 분산의심 | 20일 누적거래량 급증하며 가격 하락 |
| ⚠️ 거래량없는돌파 | 돌파했지만 평균 거래량 미달 |
| 🚨 하락거래량급증 | 하락 중 거래량 2배 이상, 매도 위험 |
| 🔻 하락가속 | 구름대아래+모멘텀↓ |
| 🔽 추세하락 | 구름대아래+이격률↓ |
| 📉 매도관심 | 하락전환 총점≤-3 |
| 🧊 적극매도 | 이탈+모멘텀↓ 총점≤-5 |
""")
start_btn = st.sidebar.button("🚀 분석 시작")

market_trend_area = st.empty()
if 'market_trend' in st.session_state:
    market_trend_area.info(st.session_state['market_trend'])

st.subheader("📊 진단 및 필터링")
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
total_metric, buy_metric, entry_metric = c1.empty(), c2.empty(), c3.empty()
accum_metric = c4.empty()
caution_metric, fall_metric, sell_metric = c5.empty(), c6.empty(), c7.empty()

total_metric.metric("전체", "0개")
buy_metric.metric("매수계열", "0개")
entry_metric.metric("진입준비", "0개")
accum_metric.metric("매집", "0개")
caution_metric.metric("구름대주의","0개")
fall_metric.metric("하락계열", "0개")
sell_metric.metric("매도관심↓", "0개")

fb1,fb2,fb3,fb4,fb5,fb6,fb7,fb8,fb9 = st.columns(9)
if 'filter' not in st.session_state:
    st.session_state.filter = "전체"

if fb1.button("🔄전체", use_container_width=True): st.session_state.filter = "전체"
if fb2.button("🔥📈매수", use_container_width=True): st.session_state.filter = "매수"
if fb3.button("🎯매집", use_container_width=True): st.session_state.filter = "매집"
if fb4.button("🌱진입준비", use_container_width=True): st.session_state.filter = "진입준비"
if fb5.button("🔄바닥탐색", use_container_width=True): st.session_state.filter = "바닥탐색"
if fb6.button("🛡️홀딩", use_container_width=True): st.session_state.filter = "홀딩"
if fb7.button("⚠️구름주의", use_container_width=True): st.session_state.filter = "구름대주의"
if fb8.button("🔻하락가속", use_container_width=True): st.session_state.filter = "하락가속"
if fb9.button("📉🧊매도", use_container_width=True): st.session_state.filter = "매도"

st.markdown("---")
result_title = st.empty()
main_result_area = st.empty()

def update_metrics(df):
    buy_kw = '적극매수|매수관심|주간돌파|양·주봉 동시돌파|매집후돌파'
    fall_kw = '하락가속|추세하락|적극매도|하락거래량급증|분산의심'
    sell_kw = '매도관심|적극매도|하락거래량급증|분산의심'
    total_metric.metric("전체", f"{len(df)}개")
    buy_metric.metric("매수계열", f"{len(df[df['신호'].str.contains(buy_kw, regex=True)])}개")
    entry_metric.metric("진입준비", f"{len(df[df['신호'].str.contains('진입준비|바닥탐색', regex=True)])}개")
    accum_metric.metric("매집", f"{len(df[df['매집(20일)'].str.contains('매집', regex=False)])}개")
    caution_metric.metric("구름대주의", f"{len(df[df['신호'].str.contains('구름대주의')])}개")
    fall_metric.metric("하락계열", f"{len(df[df['신호'].str.contains(fall_kw, regex=True)])}개")
    sell_metric.metric("매도관심↓", f"{len(df[df['신호'].str.contains(sell_kw, regex=True)])}개")

def apply_filter(df, f):
    if f == "매수": return df[df['신호'].str.contains("적극매수|매수관심|주간돌파|양·주봉 동시돌파|매집후돌파", regex=True)]
    elif f == "매집": return df[df['매집(20일)'].str.contains("매집", regex=False)]
    elif f == "진입준비": return df[df['신호'].str.contains("진입준비")]
    elif f == "바닥탐색": return df[df['신호'].str.contains("바닥탐색")]
    elif f == "홀딩": return df[df['신호'].str.contains("홀딩유지|추세상승", regex=True)]
    elif f == "구름대주의": return df[df['신호'].str.contains("구름대주의")]
    elif f == "하락가속": return df[df['신호'].str.contains("하락가속|추세하락|하락거래량급증|분산의심", regex=True)]
    elif f == "매도": return df[df['신호'].str.contains("매도|하락거래량급증|분산의심", regex=True)]
    return df

if start_btn:
    st.session_state.filter = "전체"
    st.session_state['market_trend'] = get_market_trend(market)
    market_trend_area.info(st.session_state['market_trend'])
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        st.session_state['df_all'] = pd.DataFrame()

        progress_bar = st.progress(0, text="분석 시작...")
        for i, (_, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
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
        email_summary = display_df[['종목명', '현재가', '총점', '신호', '매집(20일)', '일목(일봉)', '일목(주봉)']].to_string(index=False)
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
