import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
import urllib.parse  # 아웃룩 연결을 위한 라이브러리
from datetime import datetime

# 페이지 설정
st.set_page_config(page_title="실전 20일선 스캐너", layout="wide")

# --- 데이터 수집 및 분석 함수들 (기존과 동일) ---
def get_headers():
    return {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names = [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers())
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.type_2')
            if not table: continue
            for tr in table.select('tr'):
                a = tr.find('a', href=True)
                if not a: continue
                match = re.search(r'code=(\d{6})', a['href'])
                if match:
                    codes.append(match.group(1)); names.append(a.get_text(strip=True))
            time.sleep(0.5)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names})

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
        if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

def analyze_stock(code, name):
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
        
        tech_msgs = ["20MA 위" if price > ma20 else "20MA 밑", "MACD 양수" if macd_last > 0 else "MACD 음수"]
        if price > ma20 and macd_last > 0: status, trend = "홀드", "🚀 상승 추세 유지"
        elif (prev['종가'] < prev['20MA']) and (price > ma20): status, trend = "적극 매수", "🔥 상승 엔진 점화"
        elif abs(price - ma20)/ma20 < 0.03 and macd_last > 0: status, trend = "매수 관심", "⚓ 반등 준비 구간"
        elif price < ma20 and macd_last < macd_prev: status, trend = "적극 매도", "🧊 하락 흐름 지속"
        else: status, trend = "관망", "🌊 방향 탐색 중"
        
        energy = "📈 가속도 붙음" if macd_last > macd_prev else "⚠️ 속도 줄어듦"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        return [code, name, int(price), status, " / ".join(tech_msgs), f"{trend} | {energy}", sl_tp, chart_url]
    except: return None

# -------------------------
# 3. UI 부분
# -------------------------
st.title("🛡️ 실전 20일선 스캐너")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석할 페이지 선택", options=list(range(1, 41)), default=[1])

if st.sidebar.button("분석 시작"):
    if not selected_pages:
        st.warning("페이지를 선택해주세요.")
    else:
        st.info(f"📊 {market} {selected_pages}페이지 분석을 시작합니다.")
        market_df = get_market_sum_pages(selected_pages, market)
        
        if not market_df.empty:
            results = []
            progress_bar = st.progress(0)
            result_area = st.empty()
            
            for i, (idx, row) in enumerate(market_df.iterrows()):
                res = analyze_stock(row['종목코드'], row['종목명'])
                if res:
                    results.append(res)
                    df_curr = pd.DataFrame(results, columns=['코드', '종목명', '현재가', '상태', '기술근거', '해석', '손절익절', '차트'])
                    result_area.dataframe(df_curr, use_container_width=True, column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기")}, hide_index=True)
                progress_bar.progress((i + 1) / len(market_df))
            
            st.success("✅ 분석 완료!")
            # 결과 저장 (아웃룩 버튼 생성용)
            st.session_state['final_df'] = df_curr

# --- 분석 완료 후 나타나는 아웃룩 버튼 ---
if 'final_df' in st.session_state:
    st.markdown("---")
    st.subheader("📬 결과를 이메일로 보내기")
    
    df = st.session_state['final_df']
    # 매수 신호가 있는 종목만 요약
    buys = df[df['상태'].str.contains("매수")][['종목명', '현재가', '해석']]
    
    email_text = f"📊 주식 분석 보고서 ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    if not buys.empty:
        email_text += "[오늘의 주요 매수 신호]\n"
        for _, r in buys.iterrows():
            email_text += f"- {r['종목명']}: {r['현재가']}원 ({r['해석']})\n"
    else:
        email_text += "특이 매수 종목 없음.\n"
    
    email_text += "\n상세 내용은 스캐너 앱에서 확인하세요."
    
    # URL 인코딩
    subject = urllib.parse.quote(f"주식 분석 보고서_{datetime.now().strftime('%m%d')}")
    body = urllib.parse.quote(email_text)
    mailto_url = f"mailto:?subject={subject}&body={body}"
    
    # 아웃룩 스타일 버튼 (HTML)
    st.markdown(f"""
        <a href="{mailto_url}" target="_self" style="text-decoration: none;">
            <div style="background-color: #0078d4; color: white; padding: 12px; border-radius: 8px; text-align: center; font-weight: bold; cursor: pointer;">
                📧 Outlook으로 결과 전송하기
            </div>
        </a>
    """, unsafe_allow_html=True)
            
            status_text.success(f"✅ 선택한 모든 페이지({selected_pages}) 분석 완료!")
            st.download_button("결과 저장 (CSV)", df_curr.to_csv(index=False).encode('utf-8-sig'), f"scan_result.csv")

