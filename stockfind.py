import tkinter as tk
from tkinter import ttk, messagebox
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
from datetime import datetime
import webbrowser
import threading
import numpy as np
import time

# -------------------------
# Naver 시가총액 페이지 기반 종목 불러오기
# -------------------------
def get_market_sum_pages(pages, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []

    for page in pages:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
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
            m = re.search(r'code=(\d{6})', a['href'])
            if not m:
                continue
            code = m.group(1)
            name = a.get_text(strip=True)
            span = tds[4].find('span')
            change = span.get_text(strip=True) if span else ''
            codes.append(code)
            names.append(name)
            changes.append(change)
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률(%)': changes})

# -------------------------
# 네이버 일봉 데이터 가져오기
# -------------------------
def get_price_data(code, max_pages=20):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        pg_url = f"{url}&page={page}"
        res = requests.get(pg_url, headers={'User-agent': 'Mozilla/5.0'})
        try:
            df = pd.read_html(res.text, encoding='euc-kr')[0]
        except:
            continue
        dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df = df.dropna(how='all')
    if '날짜' not in df.columns:
        return pd.DataFrame()
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','시가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    df = df.dropna(subset=['날짜','종가','거래량'])
    df = df.sort_values('날짜').reset_index(drop=True)
    return df

# -------------------------
# 브레이크아웃 체크
# -------------------------
def check_breakout(code, name, n_confirm=2, atr_multiplier_sl=2.0, tp_muls=(2.0, 4.0)):
    import numpy as np
    import pandas as pd

    print(f">>> 분석 시작: {name} ({code})")
    try:
        df = get_price_data(code)
        print(df.head())
        if df is None or len(df) < 40:
            debug = "데이터부족"
            print(f">>> {name}: {debug}")
            return (code, name, '-', '-', '-', '-', '데이터부족', debug)

        # ----------------------------
        # 기술 지표 계산
        # ----------------------------
        df['20MA'] = df['종가'].rolling(20).mean()
        df['std20'] = df['종가'].rolling(20).std()
        df['upper'] = df['20MA'] + df['std20']*2
        df['lower'] = df['20MA'] - df['std20']*2

        df['EMA20'] = df['종가'].ewm(span=20, adjust=False).mean()
        df['EMA60'] = df['종가'].ewm(span=60, adjust=False).mean()
        df['EMA120'] = df['종가'].ewm(span=120, adjust=False).mean()

        df['vol_ma5'] = df['거래량'].rolling(5).mean()
        df['vol_ma20'] = df['거래량'].rolling(20).mean()

        # RSI
        delta = df['종가'].diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        ema_up = up.ewm(span=14, adjust=False).mean()
        ema_down = down.ewm(span=14, adjust=False).mean()
        rs = ema_up / ema_down.replace(0,1e-8)
        df['RSI14'] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['MACD_sig'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_sig']

        # ATR
        df['prev_close'] = df['종가'].shift(1)
        df['tr1'] = df['고가'] - df['저가']
        df['tr2'] = (df['고가'] - df['prev_close']).abs()
        df['tr3'] = (df['저가'] - df['prev_close']).abs()
        df['TR'] = df[['tr1','tr2','tr3']].max(axis=1)
        df['ATR14'] = df['TR'].rolling(14).mean()

        # range_pct_10
        recent_high = df['고가'].rolling(10).max()
        recent_low = df['저가'].rolling(10).min()
        df['range_pct_10'] = np.where(pd.notna(df['20MA']) & (df['20MA'] != 0),
                                      (recent_high - recent_low) / df['20MA'],
                                      np.nan)

        # ----------------------------
        # 최근 데이터
        # ----------------------------
        last = df.iloc[-1]
        prev = df.iloc[-2]

        price = last['종가']
        ma20 = last['20MA'] if pd.notna(last['20MA']) else None
        diff = round(price - ma20,2) if ma20 else '-'
        rate = f"{round((diff/ma20)*100,2)}%" if ma20 else '-'

        # ----------------------------
        # 5일 가격 추세
        # ----------------------------
        recent5_price = df['종가'].iloc[-5:]
        price_up_trend = recent5_price.is_monotonic_increasing
        price_down_trend = recent5_price.is_monotonic_decreasing

        # MACD_hist
        macd_last = last['MACD_hist'] if pd.notna(last['MACD_hist']) else 0
        macd_prev = prev['MACD_hist'] if pd.notna(prev['MACD_hist']) else 0
        macd_up_trend = macd_last > macd_prev
        macd_down_trend = macd_last < macd_prev

        # 20MA 돌파/이탈
        crossed_up = (prev['종가'] < prev['20MA']) and (last['종가'] > last['20MA'])
        crossed_down = (prev['종가'] > prev['20MA']) and (last['종가'] < last['20MA'])
        approaching_20 = pd.notna(ma20) and abs(price - ma20)/ma20 < 0.03

        # 거래량 스파이크
        vol_spike = last['거래량'] > (last['vol_ma5']*1.2 if pd.notna(last['vol_ma5']) else 0)

        rsi = last['RSI14'] if pd.notna(last['RSI14']) else None

        status = "홀드"
        debug_msgs = []

        # ----------------------------
        # 1) 홀드: 지난 5일 가격 20MA 위 + MACD 양전
        # ----------------------------
        if price_up_trend and pd.notna(ma20) and price > ma20 and macd_last > 0:
            status = "홀드"
            debug_msgs.append("상승추세+20MA 위+MACD 양전")

        # ----------------------------
        # 2) 매수 관심: MACD 0 아래에서 2일 양전 + 20MA 밑 + 가격 상승 추세 + 20MA 접근
        # ----------------------------
        elif macd_last > 0 and macd_prev > 0 and price < ma20 and price_up_trend and approaching_20:
            status = "매수 관심"
            debug_msgs.append("MACD 붉은색 2일 이상 + 20MA 밑 + 상승추세 + 20MA 접근")

        # ----------------------------
        # 3) 적극 관심 / 매수 확정: MACD 0 이상 돌파 + 20MA 돌파 + 거래량
        # ----------------------------
        elif macd_last > 0 and crossed_up:
            if vol_spike:
                status = "적극 매수"
                debug_msgs.append("MACD 양전 + 20MA 돌파 + 거래량 상승")
            else:
                status = "적극 관심"
                debug_msgs.append("MACD 양전 + 20MA 돌파")

        # ----------------------------
        # 4) 매도 관심: 지난 5일 최고가 대비 6% 이상 하락 + MACD 감소
        # ----------------------------
        recent_high5 = df['고가'].iloc[-5:].max()
        if price < recent_high5 * 0.94 and macd_down_trend:
            status = "매도 관심"
            debug_msgs.append("최근 최고가 대비 하락 >6% + MACD 하락")

        # ----------------------------
        # 5) 적극 매도: 20MA 하락 돌파 + MACD 감소
        # ----------------------------
        if crossed_down and macd_down_trend:
            status = "적극 매도"
            debug_msgs.append("20MA 하락 돌파 + MACD 하락")

        # ----------------------------
        # ATR 기반 SL/TP
        # ----------------------------
        atr = last['ATR14']
        if atr and price:
            sl = round(price - atr * atr_multiplier_sl, 2)
            tp1 = round(price + atr * tp_muls[0], 2)
            tp2 = round(price + atr * tp_muls[1], 2)
            debug_msgs.append(f"손절:{sl}")
            debug_msgs.append(f"익절1:{tp1}")
            debug_msgs.append(f"익절2:{tp2}")

        debug_msg = ";".join(debug_msgs)
        print(f">>> 완료: {name} ({code}) -> 상태: {status}  {debug_msg}")

        return (code, name, round(price,2), round(ma20,2) if ma20 else '-', diff, rate, status, debug_msg)

    except Exception as e:
        err = f"예외:{str(e)[:120]}"
        print(f">>> 오류: {name} ({code}) - {err}")
        return (code, name, '-', '-', '-', '-', '에러', err)


# -------------------------
# GUI 클래스 (단일정의)
# -------------------------
class BreakoutApp:
    def __init__(self, root):
        self.root = root
        self.root.title("실전 20일선 스캐너 (개선판)")
        self.root.geometry("1280x720")
        self.scanning = False
        self.df_result = pd.DataFrame()

        # top controls
        frame_top = tk.Frame(root)
        frame_top.pack(pady=6)
        self.market_var = tk.StringVar(value="KOSPI")
        tk.Radiobutton(frame_top, text="코스피", variable=self.market_var, value="KOSPI").pack(side='left', padx=4)
        tk.Radiobutton(frame_top, text="코스닥", variable=self.market_var, value="KOSDAQ").pack(side='left', padx=4)
        tk.Label(frame_top, text="페이지(쉼표)").pack(side='left', padx=6)
        self.page_entry = tk.Entry(frame_top, width=12); self.page_entry.insert(0,"1"); self.page_entry.pack(side='left')
        tk.Button(frame_top, text="조회", command=self.scan_market_async).pack(side='left', padx=6)
        tk.Button(frame_top, text="결과 저장", command=self.save_csv).pack(side='left', padx=6)

        # filter buttons
        frame_filter = tk.Frame(root); frame_filter.pack(pady=6)
        tk.Button(frame_filter, text="전체 보기", width=12, command=self.load_all).pack(side='left', padx=3)
        tk.Button(frame_filter, text="매수 관심", width=12, command=lambda: self.filter_status("매수 관심")).pack(side='left', padx=3)
        tk.Button(frame_filter, text="매수 확정", width=12, command=lambda: self.filter_status("매수 확정")).pack(side='left', padx=3)
        tk.Button(frame_filter, text="매도 관심", width=12, command=lambda: self.filter_status("매도 관심")).pack(side='left', padx=3)
        tk.Button(frame_filter, text="적극 매도", width=12, command=lambda: self.filter_status("적극 매도")).pack(side='left', padx=3)
        tk.Button(frame_filter, text="홀드", width=12, command=lambda: self.filter_status("홀드")).pack(side='left', padx=3)

        # treeview columns: include debug
        cols = ('순위','종목코드','종목명','등락률(%)','현재가','20MA','차이','이격률','상태','debug')
        headers = ['순위','종목코드','종목명','등락률(%)','현재가','20MA','차이','이격률','상태','debug']
        widths = [50,100,200,90,90,90,90,90,130,260]
        self.tree = ttk.Treeview(root, columns=cols, show='headings')
        for col, h, w in zip(cols, headers, widths):
            self.tree.heading(col, text=h)
            self.tree.column(col, width=w, anchor='center')
        self.tree.pack(fill='both', expand=True, pady=8)
        self.tree.tag_configure('up', foreground='#e74c3c')
        self.tree.tag_configure('down', foreground='#3498db')
        self.tree.tag_configure('interest', foreground='#f39c12')
        self.tree.tag_configure('normal', foreground='black')
        self.tree.bind("<Double-1>", self.open_stock_page)

    def load_all(self):
        if self.df_result.empty:
            messagebox.showwarning("오류","먼저 스캔을 수행하세요.")
            return
        self.display_dataframe(self.df_result)

    def scan_market_async(self):
        if self.scanning:
            messagebox.showinfo("진행중","이미 스캔중입니다.")
            return
        t = threading.Thread(target=self.scan_market)
        t.daemon = True
        t.start()

    def scan_market(self):
        self.scanning = True
        try:
            self.tree.delete(*self.tree.get_children())
            page_text = self.page_entry.get().strip()
            pages = [int(p.strip()) for p in page_text.split(',') if p.strip().isdigit()]
            if not pages: pages=[1]
            market = self.market_var.get()
            df_market = get_market_sum_pages(pages, market=market)
            if df_market.empty:
                messagebox.showwarning("경고","종목을 불러오지 못했습니다.")
                return
            results = []
            total = len(df_market)
            for idx, (_, row) in enumerate(df_market.iterrows(), start=1):
                code = row['종목코드']; name = row['종목명']
                # 진행 로그 (터미널)
                print(f"[{idx}/{total}] {name} ({code}) 분석 중...")
                res = check_breakout(code, name)
                # 기대하는 8개 unpack
                code, name, price, ma20, diff, rate, status, debug = res
                display_row = (idx, code, name, row.get('등락률(%)','-'), price, ma20, diff, rate, status, debug)
                tag = 'up' if '매수' in str(status) else 'down' if '매도' in str(status) else 'normal'
                if '관심' in str(status): tag='interest'
                self.tree.insert('', 'end', values=display_row, tags=(tag,))
                results.append(display_row)
                self.root.update_idletasks()

                # ✅ 요청 간 딜레이 (랜덤으로 0.5~1.5초)
                time.sleep(np.random.uniform(0.5, 1.5))

            # DataFrame 저장
            self.df_result = pd.DataFrame(results, columns=['순위','종목코드','종목명','등락률(%)','현재가','20MA','차이','이격률','상태','debug'])
            messagebox.showinfo("완료",f"{market} 스캔 완료: {len(self.df_result)}개")
        finally:
            self.scanning = False

    def display_dataframe(self, df):
        self.tree.delete(*self.tree.get_children())
        for _, row in df.iterrows():
            tag = 'up' if '매수' in str(row['상태']) else 'down' if '매도' in str(row['상태']) else 'normal'
            if '관심' in str(row['상태']): tag='interest'
            self.tree.insert('', 'end', values=tuple(row), tags=(tag,))

    def filter_status(self, kw):
        if self.df_result.empty:
            messagebox.showwarning("오류","먼저 스캔을 실행하세요.")
            return
        df_filtered = self.df_result if kw=="" else self.df_result[self.df_result['상태'].astype(str).str.contains(kw, na=False)]
        self.display_dataframe(df_filtered)

    def save_csv(self):
        if self.df_result.empty:
            messagebox.showwarning("저장 실패","표시된 데이터가 없습니다.")
            return
        fname = f"20일선_스캔결과_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        self.df_result.to_csv(fname, index=False, encoding='utf-8-sig')
        messagebox.showinfo("완료", f"{fname} 저장됨")

    def open_stock_page(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        item_id = selection[0]
        item_values = self.tree.item(item_id, "values")
        code = item_values[1]
        url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        webbrowser.open(url)

# -------------------------
if __name__ == "__main__":
    root = tk.Tk()
    app = BreakoutApp(root)
    root.mainloop()
