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


# ─────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────

def calc_rsi(series, period=14):
    """RSI 계산 (Wilder 방식)"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_bollinger(series, period=20, std_mult=2):
    """볼린저 밴드 + 밴드폭 계산"""
    ma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    bandwidth = (upper - lower) / ma * 100  # 밴드폭(%)
    return upper, lower, bandwidth


def get_bb_squeeze_status(bandwidth_series):
    """
    밴드폭 최근 20일 중 현재 위치로 수축/팽창 판단
      - 수축(Squeeze) : 현재 밴드폭이 최근 20일 중 하위 20%
      - 팽창(Expansion): 현재 밴드폭이 최근 20일 중 상위 20%
    """
    recent = bandwidth_series.iloc[-20:]
    cur = bandwidth_series.iloc[-1]
    p20 = recent.quantile(0.20)
    p80 = recent.quantile(0.80)
    if cur <= p20:
        return "⚡ 수축(폭발 대기)", True   # squeeze=True
    elif cur >= p80:
        return "💥 팽창(추세 진행)", False
    else:
        return "➖ 보통", False


# ─────────────────────────────────────────────
# 점수 기반 신호 결정
# ─────────────────────────────────────────────

def calc_signal_score(last, prev, ichimoku_status, rsi_val, disparity, bw_series, price_col='종가'):
    """
    각 지표별 점수 합산 → 7단계 종합 신호 반환
    
    [신호 7단계]
    🔥 적극매수   : 눌림목 완료 후 재상승 초입. 가장 이상적인 진입 타이밍
    📈 매수관심   : 상승 초입, 아직 많이 오르지 않은 상태
    🚀 추세추종   : 이미 많이 올랐지만 추세 강함. 추격 가능하나 리스크 있음
    🔄 눌림목대기 : 하락 중이지만 지지선 근처, 반등 준비 가능성
    ⏸️ 관망       : 방향 불명확
    📉 매도관심   : 하락 추세 진입
    🧊 적극매도   : 강한 하락 신호
    
    반환: (총점, 신호문자열, 점수내역dict)
    """
    score = 0
    detail = {}
    price = last[price_col]

    # ── 1. 일목균형표 (±3) ──────────────────
    if '최근 상향돌파' in ichimoku_status:
        s = 3
    elif '구름대 위' in ichimoku_status:
        s = 1
    elif '구름대 진입' in ichimoku_status:
        s = 0
    elif '구름대 아래' in ichimoku_status:
        s = -1
    elif '최근 하향이탈' in ichimoku_status:
        s = -3
    else:
        s = 0
    score += s
    detail['일목'] = s

    # ── 2. MACD 히스토그램 (±2) ─────────────
    hist_now  = last['MACD_hist']
    hist_prev = prev['MACD_hist']
    if hist_now > 0 and hist_prev <= 0:
        s = 2    # 음→양 전환 (골든)
    elif hist_now > 0:
        s = 1    # 양수 유지
    elif hist_now < 0 and hist_prev >= 0:
        s = -2   # 양→음 전환 (데드)
    else:
        s = -1   # 음수 유지
    score += s
    detail['MACD'] = s

    # ── 3. 이동평균 정배열 (±2) ─────────────
    s = 0
    if price > last['60MA']:
        s += 1    # 장기 상승 구조
    else:
        s -= 1
    if price > last['20MA']:
        s += 0.5
    else:
        s -= 0.5
    if last['5MA'] > last['20MA']:
        s += 0.5  # 단기 정배열
    else:
        s -= 0.5
    s = round(s)
    score += s
    detail['MA'] = s

    # ── 4. RSI (±2) ──────────────────────────
    if rsi_val <= 30:
        s = 2    # 과매도 → 반등 기대
    elif rsi_val <= 45:
        s = 1
    elif rsi_val <= 55:
        s = 0    # 중립
    elif rsi_val <= 70:
        s = -1
    else:
        s = -2   # 과매수 → 조정 위험
    score += s
    detail['RSI'] = s

    # ── 5. 거래량 (±1) ───────────────────────
    vol_ratio = last.get('vol_ratio', np.nan)
    if not pd.isna(vol_ratio):
        if vol_ratio >= 2.0:
            s = 1
        elif vol_ratio < 0.5:
            s = -1
        else:
            s = 0
    else:
        s = 0
    score += s
    detail['거래량'] = s

    # ── 장기 하락 구조 페널티 (-2) ───────────
    if price < last['60MA'] and last['MACD'] < 0:
        score -= 2
        detail['하락페널티'] = -2
    else:
        detail['하락페널티'] = 0

    # ════════════════════════════════════════
    # 7단계 신호 결정
    # ════════════════════════════════════════

    # [조건 사전 계산]
    is_overbought   = rsi_val > 70                    # 과매수 상태
    is_high_disp    = disparity > 12                  # 이격률 12% 초과 (많이 오름)
    is_oversold     = rsi_val < 45                    # 과매도/관심 구간
    is_near_20ma    = abs(disparity) <= 4             # 20MA 근처 (눌림목 지점)
    is_above_cloud  = '구름대 위' in ichimoku_status or '상향돌파' in ichimoku_status
    is_below_cloud  = '구름대 아래' in ichimoku_status or '하향이탈' in ichimoku_status
    macd_golden     = hist_now > 0 and hist_prev <= 0 # MACD 방금 골든
    macd_positive   = hist_now > 0                    # MACD 양수

    # ── 🔥 적극매수 ──────────────────────────
    # 눌림목 완료 후 반등 초입: 구름대 위 + 20MA 근처 + RSI 과매도탈출 + MACD 골든
    if (score >= 4
            and is_above_cloud
            and is_near_20ma
            and is_oversold
            and macd_golden):
        signal = "🔥 적극매수"

    # ── 🚀 추세추종 ──────────────────────────
    # 점수는 높지만(매수 신호) 이미 많이 오른 상태 → 추격 리스크 경고
    elif (score >= 2
            and (is_overbought or is_high_disp)):
        signal = "🚀 추세추종"

    # ── 📈 매수관심 ──────────────────────────
    # 점수 높고, 과매수/고이격 아닌 상태 → 진입 초입
    elif score >= 2:
        signal = "📈 매수관심"

    # ── 🔄 눌림목대기 ─────────────────────────
    # 점수는 낮지만 구름대 위 + 20MA 근처 + RSI 관심구간 → 반등 준비
    elif (score >= -1
            and is_above_cloud
            and is_near_20ma
            and rsi_val <= 55):
        signal = "🔄 눌림목대기"

    # ── ⏸️ 관망 ──────────────────────────────
    elif score >= -1:
        signal = "⏸️ 관망"

    # ── 📉 매도관심 ──────────────────────────
    elif score >= -4:
        signal = "📉 매도관심"

    # ── 🧊 적극매도 ──────────────────────────
    else:
        signal = "🧊 적극매도"

    return score, signal, detail


# ─────────────────────────────────────────────
# 종목 분석 메인
# ─────────────────────────────────────────────

def analyze_stock(code, name, current_change):
    try:
        df_price = get_price_data(code, max_pages=25)
        if df_price is None or len(df_price) < 80:
            return None

        df = df_price.set_index('날짜').copy()

        # ── 이동평균 ─────────────────────────
        df['5MA']  = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['60MA'] = df['종가'].rolling(60).mean()

        # ── 일목균형표 ───────────────────────
        high_9  = df['고가'].rolling(9).max()
        low_9   = df['저가'].rolling(9).min()
        df['tenkan_sen'] = (high_9 + low_9) / 2

        high_26 = df['고가'].rolling(26).max()
        low_26  = df['저가'].rolling(26).min()
        df['kijun_sen'] = (high_26 + low_26) / 2

        high_52 = df['고가'].rolling(52).max()
        low_52  = df['저가'].rolling(52).min()
        df['senkou_b_base'] = (high_52 + low_52) / 2

        # ── MACD ────────────────────────────
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD']        = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist']   = df['MACD'] - df['MACD_Signal']

        # ── RSI ─────────────────────────────
        df['RSI'] = calc_rsi(df['종가'])

        # ── 볼린저 밴드 ──────────────────────
        df['BB_upper'], df['BB_lower'], df['BB_width'] = calc_bollinger(df['종가'])

        # ── 거래량 비율 (20일 평균 대비) ─────
        df['vol_ratio'] = df['거래량'] / df['거래량'].rolling(20).mean()

        # ── 선행스팬 시프트 (거래일 기준 26봉) ─
        df_future = pd.DataFrame(index=df.index)
        df_future['senkou_a'] = (df['tenkan_sen'] + df['kijun_sen']) / 2
        df_future['senkou_b'] = df['senkou_b_base']
        df_future = df_future.shift(26)  # 거래일 기준 26봉 앞

        df_merged = pd.merge(df, df_future, left_index=True, right_index=True, how='left')
        df_final  = df_merged.dropna(subset=['senkou_a', 'senkou_b', 'RSI', 'BB_width']).copy()

        if len(df_final) < 4:
            return None

        last = df_final.iloc[-1]
        prev = df_final.iloc[-2]

        # ── 일목 상태 ────────────────────────
        price_today     = last['종가']
        cloud_top_today = max(last['senkou_a'], last['senkou_b'])
        cloud_bot_today = min(last['senkou_a'], last['senkou_b'])
        price_yesterday = prev['종가']
        cloud_top_yest  = max(prev['senkou_a'], prev['senkou_b'])

        if price_today > cloud_top_today:
            if price_yesterday <= cloud_top_yest:
                ichimoku_status = "🔥 최근 상향돌파"
            else:
                ichimoku_status = "📈 구름대 위"
        elif price_today < cloud_bot_today:
            if price_yesterday >= cloud_top_yest:
                ichimoku_status = "🧊 최근 하향이탈"
            else:
                ichimoku_status = "📉 구름대 아래"
        else:
            ichimoku_status = "🌫️ 구름대 진입"

        # ── MA 크로스 상태 ───────────────────
        def ma_cross(l, p, ma_col):
            if p[price_col] <= p[ma_col] and l[price_col] > l[ma_col]:
                return "🔥GC"
            if p[price_col] >= p[ma_col] and l[price_col] < l[ma_col]:
                return "🧊DC"
            return "📈↑" if l[price_col] > l[ma_col] else "📉↓"

        price_col = '종가'
        ma_text = f"5:{ma_cross(last,prev,'5MA')} 20:{ma_cross(last,prev,'20MA')} 60:{ma_cross(last,prev,'60MA')}"

        # ── RSI 표시 ─────────────────────────
        rsi_val = round(last['RSI'], 1)
        if rsi_val <= 30:
            rsi_display = f"{rsi_val} 🟢과매도"
        elif rsi_val <= 45:
            rsi_display = f"{rsi_val} 🔵관심"
        elif rsi_val <= 55:
            rsi_display = f"{rsi_val} ⚪중립"
        elif rsi_val <= 70:
            rsi_display = f"{rsi_val} 🟡주의"
        else:
            rsi_display = f"{rsi_val} 🔴과매수"

        # ── BB Squeeze 상태 ──────────────────
        bb_status, is_squeeze = get_bb_squeeze_status(df_final['BB_width'])

        # 현재가가 밴드 어느 위치인지
        if last['종가'] >= last['BB_upper']:
            bb_pos = "상단터치"
        elif last['종가'] <= last['BB_lower']:
            bb_pos = "하단터치"
        else:
            bb_pos = "밴드내부"
        bb_display = f"{bb_status} / {bb_pos}"

        # ── 거래량 표시 ──────────────────────
        vol_r = round(last['vol_ratio'], 1) if not pd.isna(last['vol_ratio']) else 1.0
        if vol_r >= 2.0:
            vol_display = f"{vol_r}배 📈"
        elif vol_r < 0.5:
            vol_display = f"{vol_r}배 📉"
        else:
            vol_display = f"{vol_r}배"

        # ── 이격률 ───────────────────────────
        disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"

        # ── 점수 기반 종합 신호 ──────────────
        score, signal, detail = calc_signal_score(
            last, prev, ichimoku_status, rsi_val, disparity, df_final['BB_width']
        )

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [
            code, name, current_change,
            int(last['종가']), disparity_fmt,
            score, signal,
            ichimoku_status, ma_text,
            rsi_display, bb_display, vol_display,
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
           'RSI', 'BB상태', '거래량', '차트']


def style_signal(val):
    v = str(val)
    if '적극매수'   in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '매수관심'   in v: return 'color:#ef5350;font-weight:bold'
    if '추세추종'   in v: return 'color:white;background-color:#e65100;font-weight:bold'  # 주황 (리스크 경고)
    if '눌림목대기' in v: return 'color:white;background-color:#6a1b9a;font-weight:bold'  # 보라 (준비 대기)
    if '적극매도'   in v: return 'color:white;background-color:#1565c0;font-weight:bold'
    if '매도관심'   in v: return 'color:#42a5f5;font-weight:bold'
    return 'color:#9e9e9e'


def style_ichimoku(val):
    v = str(val)
    if '상향돌파' in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '하향이탈' in v: return 'color:white;background-color:#1565c0;font-weight:bold'
    if '구름대 위' in v: return 'color:#ef5350'
    if '구름대 아래' in v: return 'color:#64b5f6'
    return 'color:#9e9e9e'


def style_rsi(val):
    v = str(val)
    if '과매도' in v: return 'color:#43a047;font-weight:bold'
    if '🔵' in v:     return 'color:#1e88e5'
    if '과매수' in v: return 'color:#e53935;font-weight:bold'
    if '🟡' in v:     return 'color:#fb8c00'
    return ''


def style_score(val):
    try:
        v = int(val)
        if v >= 5:  return 'color:white;background-color:#c62828;font-weight:bold'  # 적극매수
        if v >= 2:  return 'color:#ef5350;font-weight:bold'                         # 매수관심/추세추종
        if v >= -1: return 'color:#9e9e9e'                                          # 관망/눌림목
        if v >= -4: return 'color:#42a5f5;font-weight:bold'                         # 매도관심
        return 'color:white;background-color:#1565c0;font-weight:bold'              # 적극매도
    except:
        return ''


def style_pct(val):
    v = str(val)
    if v.startswith('+') or (v[0].isdigit() and float(v.replace('%','')) > 0):
        return 'color:#ef5350'
    if '-' in v:
        return 'color:#42a5f5'
    return ''


def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다.")
        return

    dynamic_height = (len(dataframe) + 1) * 35 + 3

    styled = (
        dataframe.style
        .map(style_signal,   subset=['신호'])
        .map(style_ichimoku, subset=['일목(일봉)'])
        .map(style_rsi,      subset=['RSI'])
        .map(style_score,    subset=['총점'])
        .map(style_pct,      subset=['등락률', '이격률'])
        .map(lambda x: 'color:#ef5350' if 'GC' in str(x) else ('color:#42a5f5' if 'DC' in str(x) else ''), subset=['MA크로스'])
        .map(lambda x: 'color:#ef9a00;font-weight:bold' if '수축' in str(x) else ('color:#26a69a' if '팽창' in str(x) else ''), subset=['BB상태'])
        .map(lambda x: 'color:#ef5350' if '📈' in str(x) else ('color:#64b5f6' if '📉' in str(x) else ''), subset=['거래량'])
    )

    st.dataframe(
        styled,
        use_container_width=True,
        height=dynamic_height,
        column_config={
            "차트":   st.column_config.LinkColumn("차트", display_text="열기"),
            "코드":   st.column_config.TextColumn("코드", width="small"),
            "총점":   st.column_config.NumberColumn("총점", width="small"),
            "MA크로스": st.column_config.TextColumn("MA크로스", width="medium"),
            "BB상태": st.column_config.TextColumn("BB상태", width="large"),
            "RSI":    st.column_config.TextColumn("RSI", width="medium"),
        },
        hide_index=True
    )


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────

st.title("🛡️ 스마트 데이터 스캐너 v3")

# ── 사이드바 ─────────────────────────────────
st.sidebar.header("설정")
market         = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])

st.sidebar.markdown("---")
st.sidebar.markdown("""
**📊 7단계 신호 기준**

| 신호 | 의미 |
|------|------|
| 🔥 적극매수 | 눌림목 완료 후 재상승 초입 |
| 📈 매수관심 | 상승 초입, 안 오른 상태 |
| 🚀 추세추종 | 이미 올랐지만 추세 강함 ⚠️ |
| 🔄 눌림목대기 | 구름대 위, 20MA 근처 반등 준비 |
| ⏸️ 관망 | 방향 불명확 |
| 📉 매도관심 | 하락 추세 |
| 🧊 적극매도 | 강한 하락 신호 |

> ⚠️ 추세추종은 이격률 12% 초과  
> 또는 RSI 70 이상 — 진입 시 손절 필수

**📐 점수 구성**
- 일목균형표 : ±3점
- MACD       : ±2점
- 이동평균   : ±2점
- RSI        : ±2점
- 거래량     : ±1점
- 하락페널티 : -2점
""")

start_btn = st.sidebar.button("🚀 분석 시작")

# ── 메트릭 ───────────────────────────────────
st.subheader("📊 진단 및 필터링")
c1, c2, c3, c4, c5 = st.columns(5)
total_metric   = c1.empty()
buy_metric     = c2.empty()
trend_metric   = c3.empty()
pullback_metric= c4.empty()
sell_metric    = c5.empty()

total_metric.metric("전체 종목",  "0개")
buy_metric.metric("매수관심",     "0개")
trend_metric.metric("추세추종",   "0개")
pullback_metric.metric("눌림목대기","0개")
sell_metric.metric("매도관련",    "0개")

# ── 필터 버튼 (7개) ──────────────────────────
f1, f2, f3, f4, f5, f6, f7 = st.columns(7)
if 'filter' not in st.session_state:
    st.session_state.filter = "전체"

if f1.button("🔄 전체",      use_container_width=True): st.session_state.filter = "전체"
if f2.button("🔥📈 매수관심", use_container_width=True): st.session_state.filter = "매수관심"
if f3.button("🚀 추세추종",  use_container_width=True): st.session_state.filter = "추세추종"
if f4.button("🔄 눌림목",    use_container_width=True): st.session_state.filter = "눌림목"
if f5.button("📉🧊 매도관련", use_container_width=True): st.session_state.filter = "매도"
if f6.button("⚡ BB수축",    use_container_width=True): st.session_state.filter = "수축"
if f7.button("🔴 RSI과매수", use_container_width=True): st.session_state.filter = "과매수"

st.markdown("---")
result_title    = st.empty()
main_result_area = st.empty()


# ── 분석 시작 ────────────────────────────────
def update_metrics(df):
    total_metric.metric("전체 종목",   f"{len(df)}개")
    buy_metric.metric("매수관심",
        f"{len(df[df['신호'].str.contains('적극매수|매수관심', regex=True)])}개")
    trend_metric.metric("추세추종",
        f"{len(df[df['신호'].str.contains('추세추종')])}개")
    pullback_metric.metric("눌림목대기",
        f"{len(df[df['신호'].str.contains('눌림목대기')])}개")
    sell_metric.metric("매도관련",
        f"{len(df[df['신호'].str.contains('매도')])}개")


def apply_filter(df, f):
    if f == "매수관심":
        return df[df['신호'].str.contains("적극매수|매수관심", regex=True)]
    elif f == "추세추종":
        return df[df['신호'].str.contains("추세추종")]
    elif f == "눌림목":
        return df[df['신호'].str.contains("눌림목대기")]
    elif f == "매도":
        return df[df['신호'].str.contains("매도")]
    elif f == "수축":
        return df[df['BB상태'].str.contains("수축")]
    elif f == "과매수":
        return df[df['RSI'].str.contains("과매수")]
    return df


if start_btn:
    st.session_state.filter = "전체"
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
                # 총점 기준 정렬 (높은 점수 위로)
                df_all = df_all.sort_values('총점', ascending=False).reset_index(drop=True)
                st.session_state['df_all'] = df_all

                update_metrics(df_all)

                display_df = apply_filter(df_all, st.session_state.filter)
                result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
                with main_result_area:
                    show_styled_dataframe(display_df)

            progress_bar.progress((i + 1) / len(market_df),
                                  text=f"분석 중: {row['종목명']} ({i+1}/{len(market_df)})")

        progress_bar.empty()
        st.success("✅ 분석 완료!")


# ── 필터 버튼 동작 (분석 후) ─────────────────
if not start_btn and 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = apply_filter(df, st.session_state.filter)

    update_metrics(df)
    result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
    with main_result_area:
        show_styled_dataframe(display_df)

    if not display_df.empty:
        email_summary = display_df[['종목명', '현재가', '총점', '신호', '일목(일봉)', 'RSI']].to_string(index=False)
        encoded_body  = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
        mailto_url    = f"mailto:?subject=주식리포트&body={encoded_body}"
        st.markdown(
            f'<a href="{mailto_url}" target="_self" style="text-decoration:none;">'
            f'<div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;'
            f'text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>',
            unsafe_allow_html=True
        )

elif 'df_all' not in st.session_state:
    with main_result_area:
        st.info("왼쪽 사이드바에서 '분석 시작' 버튼을 눌러주세요.")
