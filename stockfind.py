import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
from datetime import datetime

# 페이지 설정
st.set_page_config(page_title="실전 20일선 스캐너", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# -------------------------
# 1. 데이터 수집 함수 (멀티 페이지 지원)
# -------------------------
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
                    codes.append(match.group(1))
                    names.append(a.get_text(strip=True))
            time.sleep(0.8) # 페이지 간 이동 시 매너 딜레이
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
# 2. 분석 로직 (기술+직관 하이브리드)
# -------------------------
def analyze_stock(code, name):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None

        # 지표 계산
        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # ATR 계산
        df['tr'] = np.maximum(df['고가'] - df['저가'], 
                              np.maximum(abs(df['고가'] - df['종가'].shift(1)), 
                                         abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()

        last, prev = df.iloc[-1], df.iloc[-2]
        price, ma20 = last['종가'], last['20MA']
        macd_last, macd_prev = last['MACD_hist'], prev['MACD_hist']
        
        # [기술적 근거]
        tech_msgs = [
            "20MA 위" if price > ma20 else "20MA 밑",
            "MACD 양수" if macd_last > 0 else "MACD 음수",
            "에너지 증가" if macd_last > macd_prev else "에너지 감소"
        ]

        # [직관적 해석] 방향성 결정
        if price > ma20 and macd_last > 0:
            status, main_trend = "홀드", "🚀 상승 추세 유지"
        elif (prev['종가'] < prev['20MA']) and (price > ma20):
            status, main_trend = "적극 매수", "🔥 상승 엔진 점화"
        elif abs(price - ma20)/ma20 < 0.03 and macd_last > 0:
            status, main_trend = "매수 관심", "⚓ 반등 준비 구간"
        elif price < ma20 and macd_last < macd_prev:
            status, main_trend = "적극 매도", "🧊 하락 흐름 지속"
        else:
            status, main_trend = "관망", "🌊 방향 탐색 중"

        # [직관적 해석] 에너지 가속도 결정
        energy_msg = "📈 가속도 붙음" if macd_last > macd_prev else "⚠️ 속도 줄어듦"
        intuit_msgs = [main_trend, energy_msg]

        # 손익절 및 차트 링크
        atr = last['ATR']
        sl_tp = f"{int(price - atr*2)} / {int(price + atr*2)}" if pd.notna(atr) else "- / -"
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [code, name, int(price), status, " / ".join(tech_msgs), " | ".join(intuit_msgs), sl_tp, chart_url]
    except: return None

# -------------------------
# 3. UI 부분 (페이지 선택 기능 강화)
# -------------------------
st.title("🛡️ 실전 20일선 스캐너")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])

# 슬라이더 대신 멀티셀렉트로 페이지 개별 선택 가능하게 변경
selected_pages = st.sidebar.multiselect(
    "분석할 페이지 선택 (중복 선택 가능)", 
    options=list(range(1, 41)), 
    default=[1]
)

if st.sidebar.button("분석 시작"):
    if not selected_pages:
        st.warning("분석할 페이지를 최소 하나 이상 선택해 주세요.")
    else:
        st.info(f"📊 {market}의 {selected_pages} 페이지 분석을 시작합니다.")
        market_df = get_market_sum_pages(selected_pages, market)
        
        if not market_df.empty:
            results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            result_area = st.empty()
            
            total = len(market_df)
            for i, (idx, row) in enumerate(market_df.iterrows()):
                status_text.text(f"분석 중: {row['종목명']} ({i+1}/{total})")
                res = analyze_stock(row['종목코드'], row['종목명'])
                
                if res:
                    results.append(res)
                    df_curr = pd.DataFrame(results, columns=['코드', '종목명', '현재가', '상태', '기술적 근거', '직관적 해석', '손절/익절', '차트'])
                    
                    result_area.dataframe(
                        df_curr.style.applymap(
                            lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''),
                            subset=['상태']
                        ),
                        use_container_width=True,
                        column_config={
                            "차트": st.column_config.LinkColumn("네이버차트", display_text="열기"),
                        },
                        hide_index=True
                    )
                
                progress_bar.progress((i + 1) / total)
                time.sleep(1.2) # 차단 방지를 위한 시간 간격
            
            status_text.success(f"✅ 선택한 모든 페이지({selected_pages}) 분석 완료!")
            st.download_button("결과 저장 (CSV)", df_curr.to_csv(index=False).encode('utf-8-sig'), f"scan_result.csv")
