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

# 페이지 설정
st.set_page_config(page_title="실전 20일선 종합 대시보드", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# -------------------------
# 1. 데이터 수집 함수
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
                    # 등락률 텍스트 추출 (예: +1.23%)
                    change_text = tds[4].get_text(strip=True)
                    changes.append(change_text)
            time.sleep(0.7)
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
        time.sleep(0.15)
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

# -------------------------
# 2. 분석 로직 (전체 지표 통합)
# -------------------------
def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None

        # 지표 계산
        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # ATR 계산 (기존 로직 유지)
        df['tr'] = np.maximum(df['고가'] - df['저가'], 
                              np.maximum(abs(df['고가'] - df['종가'].shift(1)), 
                                         abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()

        last, prev = df.iloc[-1], df.iloc[-2]
        price = last['종가']
        ma20 = last['20MA']
        macd_last, macd_prev = last['MACD_hist'], prev['MACD_hist']
        atr = last['ATR']
        
        # 수치 지표 계산
        diff = price - ma20  # 차이
        disparity = (price / ma20) * 100  # 이격률
        sl_tp = f"{int(price - atr*2)} / {int(price + atr*2)}" if pd.notna(atr) else "- / -"

        # 해석 로직 (기존 유지)
        if price > ma20 and macd_last > 0:
            status, main_trend = "홀드", "🚀 상승 유지"
        elif (prev['종가'] < prev['20MA']) and (price > ma20):
            status, main_trend = "적극 매수", "🔥 엔진 점화"
        elif abs(price - ma20)/ma20 < 0.03 and macd_last > 0:
            status, main_trend = "매수 관심", "⚓ 반등 준비"
        elif price < ma20 and macd_last < macd_prev:
            status, main_trend = "적극 매도", "🧊 추세 꺾임"
        else:
            status, main_trend = "관망", "🌊 방향 탐색"

        energy_msg = "📈 가속" if macd_last > macd_prev else "⚠️ 감속"
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        # 컬럼 순서: 코드, 종목명, 등락률, 현재가, 20MA, 차이, 이격률, 손절/익절, 상태, 해석, 차트
        return [
            code, name, current_change, int(price), int(ma20), 
            int(diff), round(disparity, 2), sl_tp, status, 
            f"{main_trend} | {energy_msg}", chart_url
        ]
    except: return None

# -------------------------
# 3. UI 실행부
# -------------------------
st.title("🛡️ 20일선 종합 데이터 스캐너")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])

if st.sidebar.button("분석 시작"):
    if not selected_pages:
        st.warning("페이지를 선택해 주세요.")
    else:
        st.info(f"📊 {market} 분석을 시작합니다. (페이지: {selected_pages})")
        market_df = get_market_sum_pages(selected_pages, market)
        
        if not market_df.empty:
            results = []
            progress_bar = st.progress(0)
            result_area = st.empty()
            
            for i, (idx, row) in enumerate(market_df.iterrows()):
                res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
                if res:
                    results.append(res)
                    df_curr = pd.DataFrame(results, columns=[
                        '코드', '종목명', '등락률', '현재가', '20MA', 
                        '차이', '이격률', '손절/익절', '상태', '해석', '차트'
                    ])
                    
                    # 실시간 테이블 렌더링
                    result_area.dataframe(
                        df_curr.style.applymap(
                            lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''),
                            subset=['상태']
                        ).applymap(
                            lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''),
                            subset=['등락률']
                        ),
                        use_container_width=True,
                        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기")},
                        hide_index=True
                    )
                progress_bar.progress((i + 1) / len(market_df))
            
            st.success("✅ 모든 분석이 완료되었습니다!")
            st.session_state['final_df'] = df_curr

# --- Outlook 연동 버튼 ---
if 'final_df' in st.session_state:
    st.markdown("---")
    st.subheader("📬 결과를 이메일로 보내기")
    df = st.session_state['final_df']
    buys = df[df['상태'].str.contains("매수")][['종목명', '현재가', '이격률', '해석']]
    
    email_text = f"📊 주식 분석 결과 ({datetime.now().strftime('%m-%d %H:%M')})\n\n"
    if not buys.empty:
        email_text += "[오늘의 주요 매수 종목 리스트]\n"
        for _, r in buys.iterrows():
            email_text += f"- {r['종목명']}: {r['현재가']}원 (이격:{r['이격률']}%) - {r['해석']}\n"
    else:
        email_text += "특이 매수 종목이 없습니다.\n"
    
    subject = urllib.parse.quote(f"주식 분석 보고서_{datetime.now().strftime('%m%d')}")
    body = urllib.parse.quote(email_text)
    mailto_url = f"mailto:?subject={subject}&body={body}"
    
    st.markdown(f"""
        <a href="{mailto_url}" target="_self" style="text-decoration: none;">
            <div style="background-color: #0078d4; color: white; padding: 15px; border-radius: 8px; text-align: center; font-weight: bold; cursor: pointer;">
                📧 Outlook 앱으로 요약 결과 전송
            </div>
        </a>
    """, unsafe_allow_html=True)

