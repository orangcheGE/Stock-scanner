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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.naver.com/'
    }

def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers(), timeout=10)
            res.raise_for_status()
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.type_2')
            if not table: continue
            for tr in table.select('tr'):
                tds = tr.find_all('td')
                if len(tds) < 2: continue
                a = tr.find('a', href=True)
                if not a: continue
                match = re.search(r'code=(\d{6})', a['href'])
                if match:
                    codes.append(match.group(1))
                    names.append(a.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True)) # 등락률
            time.sleep(0.3)
        except requests.exceptions.RequestException:
            continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

# BUG-FIX: 더 많은 데이터를 가져오도록 max_pages 기본값을 60으로 수정
def get_price_data(code, max_pages=60):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages + 1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers(), timeout=10)
            res.raise_for_status()
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if not df_list or df_list[0].empty:
                break
            dfs.append(df_list[0].dropna(how='all'))
            time.sleep(0.05)
        except Exception:
            break
    if not dfs: return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)
    df = df.rename(columns={'날짜': '날짜', '종가': '종가', '전일비': '전일비', '시가': '시가', '고가': '고가', '저가': '저가', '거래량': '거래량'})
    numeric_cols = ['종가', '시가', '고가', '저가', '거래량']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜'] + numeric_cols).sort_values('날짜').reset_index(drop=True)


# ─────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────

def calc_cci(df, period=20):
    tp = (df['고가'] + df['저가'] + df['종가']) / 3
    ma = tp.rolling(window=period, min_periods=1).mean()
    mad = tp.rolling(window=period, min_periods=1).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * mad.replace(0, np.nan))

def detect_20ma_touch(df_final):
    """20MA 눌림목 터치 감지, (터치여부, 표시문자열) 반환"""
    try:
        last = df_final.iloc[-1]
        prev = df_final.iloc[-2]
        price = last['종가']
        ma20 = last['20MA']

        if pd.isna(ma20) or ma20 <= 0: return False, "N/A"

        disp = (price - ma20) / ma20 * 100
        prev_disp = (prev['종가'] - prev['20MA']) / prev['20MA'] * 100 if prev['20MA'] > 0 else 0

        # 골든터치: 어제 아래 -> 오늘 위
        if prev_disp < 0 and disp >= 0:
            return True, "🎯20MA골든"
        # 현재 ±3% 이내 + 상승 중
        if -3 <= disp <= 3 and price >= prev['종가']:
            return True, f"🎯20MA근접({disp:+.1f}%)"
        # 최근 5일 내 터치 후 반등
        for d in range(1, 6):
            if len(df_final) < d + 2: break
            r = df_final.iloc[-(d + 1)]
            r_disp = (r['종가'] - r['20MA']) / r['20MA'] * 100 if r['20MA'] > 0 else 0
            if -3 <= r_disp <= 3 and price > r['종가']:
                return True, f"🎯20MA+{d}일"
        return False, f"이격{disp:+.1f}%"
    except IndexError:
        return False, "-"

# NEW: 주간 연속봉 계산 함수
def detect_weekly_consecutive_candles(df_weekly, n=5):
    """주봉 데이터로 연속 양봉/음봉 감지, (연속수, 표시문자열) 반환"""
    try:
        # 주봉은 시가가 없으므로 종가로 비교
        closes = df_weekly['종가'].iloc[-n:]
        if len(closes) < 2: return 0, "➖"
        
        dirs = [1 if closes.iloc[i] > closes.iloc[i-1] else -1 for i in range(1, len(closes))]
        if not dirs: return 0, "➖"
        
        last_d = dirs[-1]
        count = sum(1 for d in reversed(dirs) if d == last_d)
        
        if last_d == 1:
            tag = f"📈{count}주연속상승" if count >= 3 else f"📈{count}주상승"
        else:
            tag = f"📉{count}주연속하락" if count >= 3 else f"📉{count}주하락"
        return count if last_d == 1 else -count, tag
    except Exception:
        return 0, "➖"

def calc_trading_amount(df_final, min_bil=30):
    """최근 5일 평균 거래대금 계산, (평균억원, 표시문자열, 충분여부) 반환"""
    try:
        last5 = df_final.iloc[-5:]
        avg = (last5['종가'] * last5['거래량']).mean() / 1e8
        if avg >= 500: disp = f"{avg:,.0f}억🔴"
        elif avg >= 100: disp = f"{avg:,.0f}억🟠"
        elif avg >= min_bil: disp = f"{avg:,.0f}억🟡"
        else: disp = f"{avg:,.0f}억"
        return avg, disp, avg >= min_bil
    except Exception:
        return 0, "-", False

def get_ichimoku_status(df, is_weekly=False):
    """일목균형표 상태 계산, (상태문자열) 반환"""
    try:
        # 기간 설정
        p1, p2, p3 = (9, 26, 52)
        
        # 전환선, 기준선, 후행스팬, 선행스팬B
        h1, l1 = df['고가'].rolling(p1).max(), df['저가'].rolling(p1).min()
        h2, l2 = df['고가'].rolling(p2).max(), df['저가'].rolling(p2).min()
        h3, l3 = df['고가'].rolling(p3).max(), df['저가'].rolling(p3).min()
        
        df['tenkan'] = (h1 + l1) / 2
        df['kijun'] = (h2 + l2) / 2
        df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(p2)
        df['senkou_b'] = ((h3 + l3) / 2).shift(p2)

        df_f = df.dropna(subset=['senkou_a', 'senkou_b']).copy()
        if len(df_f) < 2: return "데이터부족"

        last = df_f.iloc[-1]
        prev = df_f.iloc[-2]

        ct_now, cb_now = max(last['senkou_a'], last['senkou_b']), min(last['senkou_a'], last['senkou_b'])
        ct_prev, cb_prev = max(prev['senkou_a'], prev['senkou_b']), min(prev['senkou_a'], prev['senkou_b'])
        
        price_now, price_prev = last['종가'], prev['종가']

        # 현재 위치
        above_now = price_now > ct_now
        below_now = price_now < cb_now
        
        # 이전 위치
        was_below_prev = price_prev < cb_prev
        
        w = "W" if is_weekly else "" # 주봉 표시

        if above_now:
            return f"🔥{w}상향돌파" if was_below_prev else f"📈{w}구름대위"
        elif below_now:
            return f"📉{w}구름대아래"
        else:
            return f"🌫️{w}구름대내부"

    except Exception:
        return "-"

def decide_signal(ichimoku_d, ichimoku_w, ma20_touch, ma60_above):
    """단순 신호 결정 로직"""
    is_d_break = '돌파' in ichimoku_d
    is_d_above = '위' in ichimoku_d
    is_w_break = '돌파' in ichimoku_w
    is_w_above = '위' in ichimoku_w
    
    # 최우선: 일봉/주봉 동시 돌파 + 20MA 터치
    if (is_d_break or is_w_break) and ma20_touch:
        return "🎯매수타이밍"
        
    # 차선: 강한 상승 추세
    if (is_d_above and is_w_above) and ma60_above:
        if ma20_touch:
            return "📈매수준비(눌림목)"
        return "🛡️홀딩(상승추세)"
        
    # 관찰: 하나라도 돌파/위 + 60MA 위
    if (is_d_break or is_d_above or is_w_break or is_w_above) and ma60_above:
        return "🔔관찰"
        
    if '아래' in ichimoku_d and '아래' in ichimoku_w:
        return "📉추세하락"
        
    return "⏸️관망"

# ─────────────────────────────────────────────
# 종목 분석 메인
# ─────────────────────────────────────────────
def analyze_stock(code, name, current_change):
    try:
        df_price = get_price_data(code)
        if df_price is None or len(df_price) < 120: # 주봉계산위해 데이터량확보
            return None

        df = df_price.set_index('날짜').copy()

        # 이동평균
        df['20MA'] = df['종가'].rolling(20).mean()
        df['60MA'] = df['종가'].rolling(60).mean()
        
        # 일봉 일목균형표
        ichimoku_daily = get_ichimoku_status(df.copy(), is_weekly=False)

        # 주봉 변환
        df_weekly = df.resample('W-FRI').agg({
            '고가': 'max', '저가': 'min', '종가': 'last', '거래량': 'sum'
        }).dropna()

        if len(df_weekly) < 60: return None # 주봉 데이터 부족

        # 주봉 일목균형표
        ichimoku_weekly = get_ichimoku_status(df_weekly.copy(), is_weekly=True)
        
        # 주간 연속봉
        _, weekly_consec_d = detect_weekly_consecutive_candles(df_weekly)

        # 20MA 터치
        ma20_touch, ma20_d = detect_20ma_touch(df.dropna(subset=['20MA']))
        
        # 거래대금
        _, amount_d, amount_ok = calc_trading_amount(df)
        if not amount_ok: return None # 최소거래대금 미달시 제외

        # 최종 신호 결정
        last_price = df['종가'].iloc[-1]
        last_60ma = df['60MA'].iloc[-1]
        above_60ma = last_price > last_60ma if not pd.isna(last_60ma) else False
        
        signal = decide_signal(ichimoku_daily, ichimoku_weekly, ma20_touch, above_60ma)
        
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        
        return [
            code, name, int(last_price), current_change,
            signal,
            ichimoku_daily,
            ichimoku_weekly,
            ma20_d,
            weekly_consec_d,
            amount_d,
            chart_url
        ]
    except Exception:
        return None

# ─────────────────────────────────────────────
# UI 및 스타일
# ─────────────────────────────────────────────

# NEW: 단순화된 컬럼
COLUMNS = [
    '코드', '종목명', '현재가', '등락률', '신호',
    '일목(일)', '일목(주)', '20MA', '주간연속봉', '거래대금', '차트'
]

def style_signal(val):
    v = str(val)
    if '매수타이밍' in v: return 'color:white;background-color:#b71c1c;font-weight:bold'
    if '매수준비' in v: return 'color:#ef5350;font-weight:bold'
    if '관찰' in v: return 'color:#ff8f00;font-weight:bold'
    if '홀딩' in v: return 'color:#2e7d32;font-weight:bold'
    if '하락' in v: return 'color:#1565c0;font-weight:bold'
    return 'color:#9e9e9e'

def style_ichimoku(val):
    v = str(val)
    if '돌파' in v: return 'color:white;background-color:#c62828;font-weight:bold'
    if '위' in v: return 'color:#ef5350'
    if '아래' in v: return 'color:#64b5f6'
    return 'color:#9e9e9e'
    
def style_ma20(val):
    v = str(val)
    if '골든' in v: return 'color:#b71c1c;font-weight:bold'
    if '근접' in v: return 'color:#ef5350'
    if '+' in v: return 'color:#ff8f00'
    return ''

def style_weekly_consec(val):
    v = str(val)
    if '상승' in v: return 'color:#ef5350'
    if '하락' in v: return 'color:#42a5f5'
    return ''
    
def style_pct(val):
    v = str(val).strip()
    if v.startswith('+'): return 'color:#ef5350'
    if v.startswith('-'): return 'color:#42a5f5'
    return ''

st.set_page_config(layout="wide")
st.title("🛡️ 스마트 데이터 스캐너 v6")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"], key="market_select")
selected_pages = st.sidebar.multiselect("분석 페이지 선택 (1-40)", options=list(range(1, 41)), default=[1], key="page_select")
start_btn = st.sidebar.button("🚀 분석 시작")

st.sidebar.markdown("---")
st.sidebar.markdown("""
**🎯 신호 체계 (우선순위)**
1.  **매수타이밍**: 일/주 돌파 + 20MA터치
2.  **매수준비**: 일/주 구름대 위 + 눌림목
3.  **홀딩**: 강한 상승 추세
4.  **관찰**: 상승 추세 전환 가능성
5.  **관망/하락**: 그 외
""")

result_area = st.container()

if start_btn and selected_pages:
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        pb = st.progress(0, text="분석 시작...")
        for i, row in market_df.iterrows():
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
            
            pb.progress((i + 1) / len(market_df), text=f"분석 중: {row['종목명']} ({i+1}/{len(market_df)})")
        
        pb.empty()
        
        if results:
            df_all = pd.DataFrame(results, columns=COLUMNS)
            st.session_state['df_all'] = df_all
        else:
            st.warning("조건에 맞는 종목이 없습니다.")

if 'df_all' in st.session_state and not st.session_state['df_all'].empty:
    df_to_show = st.session_state['df_all']
    with result_area:
        st.info(f"총 {len(df_to_show)}개 종목이 분석되었습니다.")
        
        styled_df = (
            df_to_show.style
            .map(style_signal, subset=['신호'])
            .map(style_ichimoku, subset=['일목(일)', '일목(주)'])
            .map(style_ma20, subset=['20MA'])
            .map(style_weekly_consec, subset=['주간연속봉'])
            .map(style_pct, subset=['등락률'])
        )
        
        st.dataframe(
            styled_df,
            column_config={
                "현재가": st.column_config.NumberColumn(format="%d"),
                "차트": st.column_config.LinkColumn("차트", display_text="📊"),
            },
            use_container_width=True,
            hide_index=True
        )
else:
     with result_area:
        st.info("왼쪽 사이드바에서 '분석 시작' 버튼을 눌러주세요.")
