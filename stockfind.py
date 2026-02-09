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

# --- 분석 로직 (기능 동일) ---
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
            time.sleep(0.3)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/fchart.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers())
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list: dfs.append(df_list[0])
        except: continue
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

        # --- 1. 기본 지표 계산 ---
        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_Signal']
        
        df['tr'] = np.maximum(df['고가'] - df['저가'], np.maximum(abs(df['고가'] - df['종가'].shift(1)), abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()

        # --- 2. 분석에 필요한 변수 정의 ---
        last, prev = df.iloc[-1], df.iloc[-2]
        price, ma20 = last['종가'], last['20MA']
        macd_last, macd_prev = last['MACD_hist'], prev['MACD_hist']

        # [수정] 크로스(교차) 이벤트 정의
        price_cross_up_20ma = prev['종가'] < prev['20MA'] and price > ma20
        price_cross_down_20ma = prev['종가'] > prev['20MA'] and price < ma20
        macd_cross_up_zero = macd_prev < 0 and macd_last > 0
        macd_cross_down_zero = macd_prev > 0 and macd_last < 0

        # [수정] 최근 5일 추세(기울기) 계산
        price_slope_5d = np.polyfit(range(5), df['종가'].iloc[-5:], 1)[0]
        macd_slope_5d = np.polyfit(range(5), df['MACD_hist'].iloc[-5:], 1)[0]

        is_macd_turnaround = macd_prev < 0 and macd_last > 0
        
        # --- 3. 매수/매도/관망 상태 결정 ---
        
        # [수정] 적극 매수: 20MA 상향 돌파 + MACD 제로선 상향 돌파 (가장 강력한 신호)
        if price_cross_up_20ma and macd_cross_up_zero:
            status, trend = "적극 매수", "🔥 20MA 돌파 & MACD 양수 전환"

        # [수정] 적극 매도: 20MA 하향 이탈 + MACD 제로선 하향 돌파 (가장 강력한 신호)
        elif price_cross_down_20ma and macd_cross_down_zero:
            status, trend = "적극 매도", "🧊 20MA 이탈 & MACD 음수 전환"
      
        # [수정] 매수 관심: 20MA 향해 상승 + MACD 상승/턴어라운드
        elif price < ma20 and price_slope_5d > 0 and (macd_slope_5d > 0 or is_macd_turnaround):
            status, trend = "매수 관심", "⚓️ 반등 시도"
        
        # [신규] 매도 관심: 20MA 향해 하락 + MACD 하락
        elif price > ma20 and price_slope_5d < 0 and macd_slope_5d < 0:
            status, trend = "매도 관심", "📉 하락 전환 주의"    

        # 기존 '추가 매수/홀드' 로직 유지
        elif price > ma20 and macd_last > 0:
            disparity = ((price / ma20) - 1) * 100
            status, trend = ("추가 매수 가능", "🚀 상승세 안정적 (추가 여력)") if 0 <= disparity <= 5 else ("홀드", "📈 상승 추세 유지")
        
        else:
            status, trend = "관망", "🌊 방향 탐색"

        # --- 4. 결과 포맷팅 ---
        diff = price - ma20
        disparity = ((price / ma20) - 1) * 100
        disparity_fmt = f"{'+' if disparity > 0 else ''}{round(disparity, 2)}%"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"
        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"

        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, f"{trend} | {'📈 가속' if macd_last > macd_prev else '⚠️ 감속'}", chart_url]

    except Exception as e:
        print(f"Error analyzing {name}({code}): {e}")
        return None

def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다. 왼쪽에서 '분석 시작'을 눌러주세요.")
        return
    st.dataframe(
        dataframe.style.applymap(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# -------------------------
# UI 부분 (상시 노출 레이아웃)
# -------------------------
st.title("🛡️ 20일선 스마트 데이터 스캐너")

# 사이드바 설정
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")

# --- 메인 화면: 버튼 및 요약 섹션 (상시 노출) ---
st.subheader("📊 진단 및 필터링")
c1, c2, c3 = st.columns(3)
total_metric = c1.empty()
buy_metric = c2.empty()
sell_metric = c3.empty()

# 기본 메트릭 초기값
total_metric.metric("전체 종목", "0개")
buy_metric.metric("매수 신호", "0개")
sell_metric.metric("매도 신호", "0개")

col1, col2, col3 = st.columns(3)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
btn_all = col1.button("🔄 전체 보기", use_container_width=True)
btn_buy = col2.button("🔴 매수 관련만", use_container_width=True)
btn_sell = col3.button("🔵 매도 관련만", use_container_width=True)

if btn_all: st.session_state.filter = "전체"
if btn_buy: st.session_state.filter = "매수"
if btn_sell: st.session_state.filter = "매도"

# 실시간 분석 결과가 나타날 공간
st.markdown("---")
result_title = st.empty()
result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter})")
main_result_area = st.empty()

# 분석 실행 로직
if start_btn:
    # 1. 분석 시작 시, 이전 결과가 있다면 초기화
    if 'df_all' in st.session_state:
        del st.session_state['df_all']
    
    # 2. 새로운 분석 시작
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        progress_bar = st.progress(0, "분석을 준비 중입니다...")

        for i, (idx, row) in enumerate(market_df.iterrows()):
            # 진행률 업데이트
            progress_bar.progress((i + 1) / len(market_df), f"분석 중: {row['종목명']} ({i+1}/{len(market_df)})")

            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            
            # 분석 결과가 있을 경우에만 실시간 업데이트
            if res:
                results.append(res)
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '20MA', '차이', '이격률', '손절/익절', '상태', '해석', '차트'])
                
                # session_state에 실시간으로 저장
                st.session_state['df_all'] = df_all
                
                # 메트릭 업데이트
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 신호", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                sell_metric.metric("매도 신호", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                
                # 【핵심】 실시간 테이블 업데이트
                # for문 안에서 main_result_area에 계속 덮어쓰기하여 실시간처럼 보이게 함
                with main_result_area.container():
                    show_styled_dataframe(df_all)

        progress_bar.empty() # 진행률 바 제거
        st.success("✅ 분석 완료!")
    else:
        st.error("선택된 페이지에서 종목 정보를 가져오지 못했습니다.")

# 【핵심】 분석 시작 버튼을 누르지 않은 모든 경우 (초기 화면, 필터링 버튼 클릭 등)
else:
    # 분석된 데이터가 st.session_state에 있을 경우
    if 'df_all' in st.session_state and not st.session_state['df_all'].empty:
        df = st.session_state['df_all']
        display_df = df.copy() # 원본 데이터는 보존

        # 필터링 로직
        if st.session_state.filter == "매수":
            display_df = df[df['상태'].str.contains("매수")]
            result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}건)")
        elif st.session_state.filter == "매도":
            display_df = df[df['상태'].str.contains("매도")]
            result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}건)")
        else:
             result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}건)")

        # 필터링된 결과를 메인 영역에 표시
        with main_result_area.container():
            show_styled_dataframe(display_df)

        # Outlook 전송 버튼 (필터링된 결과 기준)
        if not display_df.empty:
            email_summary = display_df[['종목명', '현재가', '상태']].to_string(index=False)
            encoded_body = urllib.parse.quote(f"주식 분석 리포트 ({datetime.now().strftime('%Y-%m-%d')})\n\n{email_summary}")
            mailto_url = f"mailto:?subject=주식분석리포트&body={encoded_body}"
            st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)

    # 가장 처음 앱을 실행했을 때 (분석된 데이터가 없을 경우)
    else:
        with main_result_area.container():
            st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요.")






