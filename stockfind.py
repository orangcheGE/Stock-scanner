# ─────────────────────────────────────────────
# 종목 분석 메인 (수정)
# ─────────────────────────────────────────────
def analyze_stock(code, name, current_change, foreign_dict=None, fetch_investor=True):
    try:
        df_price = get_price_data(code, max_pages=25)
        if df_price is None or len(df_price) < 80:
            return None
        df = df_price.set_index('날짜').copy()

        # ... (기존 계산 로직은 동일) ...
        df['5MA']  = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['60MA'] = df['종가'].rolling(60).mean()
        
        high_9  = df['고가'].rolling(9).max()
        low_9   = df['저가'].rolling(9).min()
        df['tenkan_sen'] = (high_9 + low_9) / 2
        high_26 = df['고가'].rolling(26).max()
        low_26  = df['저가'].rolling(26).min()
        df['kijun_sen'] = (high_26 + low_26) / 2
        high_52 = df['고가'].rolling(52).max()
        low_52  = df['저가'].rolling(52).min()
        df['senkou_b_base'] = (high_52 + low_52) / 2

        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD']        = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist']   = df['MACD'] - df['MACD_Signal']
        
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

        if len(df_final) < 6:
            return None

        last  = df_final.iloc[-1]
        prev  = df_final.iloc[-2]
        prev2 = df_final.iloc[-3]
        prev3 = df_final.iloc[-4]
        prev4 = df_final.iloc[-5]
        price_col = '종가'

        # ... (ichimoku_status, ma_text, rsi_display 등 기존 계산 로직은 동일) ...
        def cloud_top(row): return max(row['senkou_a'], row['senkou_b'])
        def cloud_bot(row): return min(row['senkou_a'], row['senkou_b'])
        price_now = last['종가']
        ct_now    = cloud_top(last)
        cb_now    = cloud_bot(last)
        above_now = price_now > ct_now
        below_now = price_now < cb_now
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
            if breakout_days is not None:
                ichimoku_status = f"🔥 상향돌파({breakout_days}일전)"
            else:
                ichimoku_status = "📈 구름대 위"
        elif below_now:
            if breakdown_days is not None:
                ichimoku_status = f"🧊 하향이탈({breakdown_days}일전)"
            else:
                ichimoku_status = "📉 구름대 아래"
        else:
            prior_rows = [prev, prev2, prev3, prev4]
            was_above = any(r['종가'] > cloud_top(r) for r in prior_rows)
            was_below = any(r['종가'] < cloud_bot(r) for r in prior_rows)
            if was_above and not was_below:
                ichimoku_status = "⚠️ 구름대하락진입"
            elif was_below and not was_above:
                ichimoku_status = "🌱 구름대상승진입"
            else:
                ichimoku_status = "🌫️ 구름대 내부"
        def ma_cross(l, p, ma_col):
            if p[price_col] <= p[ma_col] and l[price_col] > l[ma_col]: return "🔥GC"
            if p[price_col] >= p[ma_col] and l[price_col] < l[ma_col]: return "🧊DC"
            return "📈↑" if l[price_col] > l[ma_col] else "📉↓"
        ma_text = (f"5:{ma_cross(last,prev,'5MA')} "
                   f"20:{ma_cross(last,prev,'20MA')} "
                   f"60:{ma_cross(last,prev,'60MA')}")
        rsi_val = round(last['RSI'], 1)
        if rsi_val <= 30: rsi_display = f"{rsi_val} 🟢과매도"
        elif rsi_val <= 45: rsi_display = f"{rsi_val} 🔵관심"
        elif rsi_val <= 55: rsi_display = f"{rsi_val} ⚪중립"
        elif rsi_val <= 70: rsi_display = f"{rsi_val} 🟡주의"
        else: rsi_display = f"{rsi_val} 🔴과매수"
        cci_now, cci_prev = last['CCI'], prev['CCI']
        cci_val  = round(cci_now, 1)
        if cci_prev < -100 and cci_now >= -100: cci_display = f"{cci_val} 🟢과매도탈출"
        elif cci_prev < 0 and cci_now >= 0: cci_display = f"{cci_val} 🔵제로크로스"
        elif cci_prev > 100 and cci_now <= 100: cci_display = f"{cci_val} 🟡과매수탈출"
        elif cci_prev > 0 and cci_now <= 0: cci_display = f"{cci_val} 🔴제로데드"
        elif cci_now > 100: cci_display = f"{cci_val} ⚡과매수"
        elif cci_now < -100: cci_display = f"{cci_val} 💧과매도"
        else: cci_display = f"{cci_val} ➖중립"
        vol_r = round(last['vol_ratio'], 1) if not pd.isna(last['vol_ratio']) else 1.0
        if vol_r >= 2.0: vol_display = f"{vol_r}배 📈"
        elif vol_r < 0.5: vol_display = f"{vol_r}배 📉"
        else: vol_display = f"{vol_r}배"
        disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        try:
            high_52 = df['종가'].rolling(252).max().iloc[-1]
            cur_p   = last['종가']
            pct_52high = round(((cur_p - high_52) / high_52) * 100, 1) if not pd.isna(high_52) and high_52 != 0 else 0.0
        except Exception:
            pct_52high = 0.0
        ma5_slope, _ = get_ma5_slope(df['종가'])
        if fetch_investor and foreign_dict is not None:
            foreign_ratio = foreign_dict.get(code, 0.0)
            investor_display = _fmt_ratio(foreign_ratio) if foreign_ratio > 0 else "-"
        else:
            foreign_ratio, investor_display = 0.0, "-"
        score, signal, detail = calc_signal_score(
            last, prev, ichimoku_status, rsi_val, cci_now, cci_prev, disparity, df_final['BB_width'],
            foreign_ratio=foreign_ratio, ma5_slope=ma5_slope, pct_from_52high=pct_52high
        )
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        # ★★★ 결과 리스트에서 bb_display, slope_display, week52_display 제거 ★★★
        return [
            code, name, current_change,
            int(last['종가']), disparity_fmt,
            score, signal,
            ichimoku_status, ma_text,
            rsi_display, cci_display, vol_display,
            investor_display,
            chart_url
        ]

    except Exception as e:
        return None

# ─────────────────────────────────────────────
# 스타일 데이터프레임 표시 (수정)
# ─────────────────────────────────────────────

# ★★★ COLUMNS 리스트 수정 ★★★
COLUMNS = ['코드', '종목명', '등락률', '현재가', '이격률',
           '총점', '신호',
           '일목(일봉)', 'MA크로스',
           'RSI', 'CCI', '거래량',
           '외국인지분율',
           '차트']

# ... (style_signal, style_ichimoku 등 다른 스타일 함수는 그대로 사용) ...

def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다.")
        return
    
    # compress_display 함수는 BB, 5MA, 52주 관련 로직이 없으므로 수정 없이 사용 가능
    disp = compress_display(dataframe)
    dynamic_height = (len(disp) + 1) * 35 + 3

    # ★★★ 스타일 적용 부분에서 BB, 5MA, 52주 관련 스타일 제거 ★★★
    styled = (
        disp.style
        .map(style_signal,   subset=['신호'])
        .map(style_ichimoku, subset=['일목(일봉)'])
        .map(style_rsi,      subset=['RSI'])
        .map(style_cci,      subset=['CCI'])
        .map(style_score,    subset=['총점'])
        .map(style_pct,      subset=['등락률', '이격률'])
        .map(lambda x: ('color:#ef9a00;font-weight:bold' if '🔥' in str(x) else
                        'color:#42a5f5;font-weight:bold' if '🧊' in str(x) else
                        'color:#ef5350' if '📈' in str(x) else
                        'color:#42a5f5' if '📉' in str(x) else ''),
             subset=['MA크로스'])
        .map(lambda x: ('color:#ef5350' if '📈' in str(x) else
                        'color:#64b5f6' if '📉' in str(x) else ''),
             subset=['거래량'])
        .map(style_investor, subset=['외국인지분율']) # 외국인 지분율은 유지
    )
    
    # ★★★ 컬럼 설정(column_config)에서 너비(width) 제거 및 불필요한 항목 제거 ★★★
    col_cfg = {
        "코드":        st.column_config.TextColumn("코드"),
        "총점":        st.column_config.NumberColumn("점수"),
        "등락률":      st.column_config.TextColumn("등락"),
        "이격률":      st.column_config.TextColumn("이격"),
        "거래량":      st.column_config.TextColumn("거래량"),
        "차트":        st.column_config.LinkColumn("차트", display_text="📊"),
        "신호":        st.column_config.TextColumn("신호"),
        "일목(일봉)":  st.column_config.TextColumn("일목"),
        "MA크로스":    st.column_config.TextColumn("MA"),
        "CCI":         st.column_config.TextColumn("CCI"),
        "종목명":      st.column_config.TextColumn("종목명"),
        "현재가":      st.column_config.NumberColumn("현재가"),
        "외국인지분율":st.column_config.TextColumn("외국인%"),
    }

    st.dataframe(
        styled,
        use_container_width=True, # 이 옵션이 컬럼 너비를 컨테이너에 맞게 조절해줍니다.
        height=dynamic_height,
        column_config=col_cfg,
        hide_index=True
    )
