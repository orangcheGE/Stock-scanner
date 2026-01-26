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

# 1. 페이지 설정
st.set_page_config(page_title="20일선 스마트 대시보드", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# -------------------------
# 2. 분석 및 수집 로직 (기능 동일)
# -------------------------
def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers())
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.type_2')
            if not table: continue
            for tr in table.select('tr'):
                tds = tr.find_all('td')
                if len(tds) < 5: continue
                a = tr.find('a', href=True)
                if not a: continue
                match = re.search(r'code=(\d{6})', a['href'])
                if match:
                    codes.append(match.group(1))
                    names.append(a.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
            time.sleep(0.5)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers())
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list: dfs.append(df_list[0])
        except: continue
        time.sleep(0.1)
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None
        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        df['tr'] = np.maximum(df['고가'] - df['저가'], np.maximum(abs(df['고가'] - df['종가'].shift(1)), abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()

        last, prev = df.iloc[-1], df.iloc[-2]
        price, ma20, macd_last, macd_prev = last['종가'], last['20MA'], last['MACD_hist'], prev['MACD_hist']
        
        diff = price - ma20
        disparity = ((price / ma20) - 1) * 100
        disparity_fmt = f"{'+' if disparity > 0 else ''}{round(disparity, 2)}%"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"

        # 🚀 스마트 진단 로직
        if price > ma20 and macd_last > 0:
            if 0 <= disparity <= 3:
                status, trend = "추가 매수 가능", "🚀 상승세 안정적 (여력 있음)"
            else:
                status, trend = "홀드", "📈 상승 추세 유지"
        elif (prev['종가'] < prev['20MA']) and (price > ma20):
            status, trend = "적극 매수", "🔥 골든크로스 발생"
        elif abs(price - ma20)/ma20 < 0.03 and macd_last > 0:
            status, trend = "매수 관심", "⚓ 20일선 지지 확인"
        elif price < ma20 and macd_last < macd_prev:
            status, trend = "적극 매도", "🧊 하락 추세 지속"
        else:
            status, trend = "관망", "🌊 방향 탐색 중"

        energy = "📈 가속" if macd_last > macd_prev else "⚠️ 감속"
        # 차트 링크를 종목 홈으로 변경하여 더 많은 정보 제공
        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"

        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, f"{trend} | {energy}", chart_url]
    except: return None

# -------------------------
# 3. UI 부분 (링크 깨짐 수정)
# -------------------------
st.title("🛡️ 20일선 스마트 데이터 스캐너")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])

# [중요] 링크 컬럼 설정을 공통 함수로 분리
def show_styled_dataframe(dataframe):
    st.dataframe(
        dataframe.style.applymap(
            lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''),
            subset=['상태']
        ).applymap(
            lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''),
            subset=['등락률', '이격률']
        ),
        use_container_width=True,
        column_config={
            "차트": st.column_config.LinkColumn("차트", display_text="열기"),
            "코드": st.column_config.TextColumn("코드", width="small")
        },
        hide_index=True
    )

if st.sidebar.button("분석 시작"):
    if not selected_pages:
        st.warning("페이지를 선택해 주세요.")
    else:
        st.info(f"📊 {market} 분석 시작...")
        market_df = get_market_sum_pages(selected_pages, market)
        if not market_df.empty:
            results = []
            progress_bar = st.progress(0)
            result_area = st.empty()
            for i, (idx, row) in enumerate(market_df.iterrows()):
                res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
                if res:
                    results.append(res)
                    df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '20MA', '차이', '이격률', '손절/익절', '상태', '해석', '차트'])
                    # 실시간 출력 시에도 링크 설정 적용
                    with result_area:
                        show_styled_dataframe(df_all)
                progress_bar.progress((i + 1) / len(market_df))
            st.success("✅ 분석 완료!")
            st.session_state['df_all'] = df_all

# --- 필터링 및 출력 ---
if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    st.markdown("---")
    
    # 요약 카드
    c1, c2, c3 = st.columns(3)
    c1.metric("전체 종목", f"{len(df)}개")
    c2.metric("매수 신호", f"{len(df[df['상태'].str.contains('매수')])}개")
    c3.metric("매도 신호", f"{len(df[df['상태'].str.contains('매도')])}개")

    # 필터 버튼
    col1, col2, col3 = st.columns(3)
    # 버튼 클릭 상태를 session_state로 관리하여 유지력 향상
    if 'filter' not in st.session_state: st.session_state.filter = "전체"
    
    if col1.button("🔄 전체 보기", use_container_width=True): st.session_state.filter = "전체"
    if col2.button("🔴 매수 관련만 보기", use_container_width=True): st.session_state.filter = "매수"
    if col3.button("🔵 매도 관련만 보기", use_container_width=True): st.session_state.filter = "매도"
    
    display_df = df.copy()
    if st.session_state.filter == "매수":
        display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "매도":
        display_df = df[df['상태'].str.contains("매도")]
    
    st.subheader(f"🔍 필터링 결과 ({st.session_state.filter})")
    show_styled_dataframe(display_df)

    # Outlook 버튼 (필터링된 결과 기반)
    email_summary = display_df[['종목명', '현재가', '상태', '해석']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식분석_리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
