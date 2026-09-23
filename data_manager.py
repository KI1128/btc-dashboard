import os
import time
import requests
import ccxt
import pandas as pd
import yfinance as yf
import datetime
import numpy as np

def fetch_and_update_data(output_path="data/btc_daily_dataset.csv"):
    os.makedirs(os.path.dirname(output_path) or "data", exist_ok=True)
    existing_df = None
    start_date = "2017-01-01"
    
    if os.path.exists(output_path):
        existing_df = pd.read_csv(output_path)
        if not existing_df.empty and 'date' in existing_df.columns:
            existing_df['date'] = pd.to_datetime(existing_df['date']).dt.date
            latest_date = existing_df['date'].max()
            start_date = (latest_date - datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    exchange = ccxt.binance()
    since = exchange.parse8601(f"{start_date}T00:00:00Z")
    all_ohlcv = []
    
    while True:
        ohlcv = exchange.fetch_ohlcv("BTC/USDT", timeframe="1d", since=since, limit=1000)
        if len(ohlcv) == 0: break
        all_ohlcv.extend(ohlcv)
        since = ohlcv[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)

    btc_df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    btc_df["date"] = pd.to_datetime(btc_df["timestamp"], unit="ms", utc=True).dt.tz_convert("Asia/Tokyo").dt.date
    btc_df = btc_df[["date", "open", "high", "low", "close", "volume"]]

    new_df = btc_df.sort_values("date").reset_index(drop=True)
    
    if existing_df is not None and not existing_df.empty:
        df = pd.concat([existing_df, new_df], ignore_index=True)
        df = df.drop_duplicates(subset="date", keep="last")
    else:
        df = new_df
        
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(output_path, index=False)
    return df

def load_data(csv_path="data/btc_daily_dataset.csv"):
    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # MAと勾配の計算
    ma_windows = [7, 30, 90, 365, 1460]
    for w in ma_windows:
        df[f'MA_{w}'] = df['close'].rolling(window=w).mean()
        
    df['slope_7'] = df['MA_7'].diff()
    df['slope_30'] = df['MA_30'].diff()
    df['ma365_slope'] = df['MA_365'].diff(5)
    
    # お天気用指標
    df['macro_spread'] = (df['MA_365'] - df['MA_1460']) / df['MA_1460'] * 100
    df['past_90d_return'] = df['close'].pct_change(periods=90) * 100
    df['future_90d_return'] = (df['close'].shift(-90) / df['close'] - 1.0) * 100
    df['daily_return'] = df['close'].pct_change()
    
    return df

def calculate_strategy(df):
    current_base = 1.0  
    current_short = 0.0
    is_short_mode_under_100 = False
    short_hold_days = 0 
    long_active = False
    days_since_long_exit = 999
    long_penalty = False

    latest_cond_perfect_bull = latest_cond_perfect_bear = latest_cond_buy_gradual = latest_cond_sell_gradual = False
    latest_c_close = latest_c_7 = latest_c_30 = latest_c_90 = latest_c_365 = latest_c_1460 = 0.0
    latest_core_action = "⚪ 現状維持 (条件不一致)"

    for i in range(len(df)):
        c_close, c_7, c_30, c_90, c_365, c_1460 = df['close'].iloc[i], df['MA_7'].iloc[i], df['MA_30'].iloc[i], df['MA_90'].iloc[i], df['MA_365'].iloc[i], df['MA_1460'].iloc[i]
        slope_7, slope_30 = df['slope_7'].iloc[i], df['slope_30'].iloc[i]

        new_base = current_base
        target_long = 0.0
        ideal_short = 0.0
        signal_short = 0.0

        if pd.notna(c_1460) and pd.notna(slope_30):
            cond_perfect_bull = (c_1460 < c_365 < c_90 < c_30 < c_7)
            cond_perfect_bear = (c_7 < c_30 < c_90 < c_365 < c_1460)
            cond_buy_gradual  = (c_7 < c_1460)
            cond_sell_gradual = (c_1460 < min(c_7, c_30)) and (max(c_7, c_30) < c_365) and (c_365 < c_90)
            
            if i == len(df) - 1:
                latest_c_close, latest_c_7, latest_c_30, latest_c_90, latest_c_365, latest_c_1460 = c_close, c_7, c_30, c_90, c_365, c_1460
                latest_cond_perfect_bull, latest_cond_perfect_bear, latest_cond_buy_gradual, latest_cond_sell_gradual = cond_perfect_bull, cond_perfect_bear, cond_buy_gradual, cond_sell_gradual
                if cond_perfect_bull: latest_core_action = "🟢 Perfect Bull (100%維持)"
                elif cond_perfect_bear: latest_core_action = "🔴 Perfect Bear (0%維持)"
                elif cond_buy_gradual: latest_core_action = "🟡 1% 買い集め進行中"
                elif cond_sell_gradual: latest_core_action = "🟠 3% 段階的売却中"

            if cond_perfect_bull: new_base = 1.0
            elif cond_perfect_bear: new_base = 0.0
            elif cond_buy_gradual: new_base = min(1.0, current_base + 0.01)
            elif cond_sell_gradual: new_base = max(0.0, current_base - 0.03)
                
            current_base = new_base
            cap = (current_base / 2.0) + (1.0 - current_base)
            
            if current_base == 1.0:
                is_short_mode_under_100 = False 
                short_cond_100 = (slope_30 < 0) and (slope_7 < 0) and ((slope_30 - slope_7) > 0) and (c_close < c_90)
                
                if short_cond_100 and short_hold_days < 7:
                    signal_short = cap * 0.25 
                    short_hold_days += 1
                else:
                    signal_short = 0.0
                    if not short_cond_100: short_hold_days = 0
                
                ideal_short = 0.0
                if current_short > ideal_short: current_short = max(ideal_short, current_short - 0.005)
                if signal_short > 0.0: long_penalty = False
                if (c_7 > c_30) and (c_30 > c_90): long_penalty = False 

                long_signal = (signal_short == 0.0) and (c_7 > c_30)
                is_long_active_today = False

                if long_signal:
                    if not long_active and (days_since_long_exit <= 7) and not ((c_7 > c_30) and (c_30 > c_90)):
                        long_penalty = True
                    if not long_penalty:
                        target_long = 0.5 if current_short > 0.0 else 1.0
                        is_long_active_today = True
                    else: target_long = 0.0
                else: target_long = 0.0
                
                if long_active and not is_long_active_today: days_since_long_exit = 0  
                elif not is_long_active_today: days_since_long_exit += 1 
                long_active = is_long_active_today

            else:
                short_hold_days = 0 
                long_active = False
                days_since_long_exit = 999
                long_penalty = False
                if (cond_perfect_bear or cond_sell_gradual): is_short_mode_under_100 = True
                if (cond_perfect_bull or cond_buy_gradual): is_short_mode_under_100 = False
                
                ideal_short = 1.0 if is_short_mode_under_100 else 0.0
                target_long = 0.0
                    
                if current_short < ideal_short: current_short = min(ideal_short, current_short + 0.015)
                elif current_short > ideal_short: current_short = max(ideal_short, current_short - 0.005)

    return {
        "core_pct": current_base * 100, "long_pct": target_long * 100, "short_pct": current_short * 100,
        "core_action": latest_core_action, "c_close": latest_c_close, "c_7": latest_c_7, "c_30": latest_c_30, 
        "c_90": latest_c_90, "c_365": latest_c_365, "c_1460": latest_c_1460,
        "cond_perfect_bull": latest_cond_perfect_bull, "cond_perfect_bear": latest_cond_perfect_bear,
        "cond_buy_gradual": latest_cond_buy_gradual, "cond_sell_gradual": latest_cond_sell_gradual,
    }

def calculate_weather(df):
    valid_xy_df = df.dropna(subset=['macro_spread', 'past_90d_return', 'MA_30', 'MA_365', 'ma365_slope']).copy()
    if len(valid_xy_df) == 0: return None
    
    mean_macro, std_macro = valid_xy_df['macro_spread'].mean(), valid_xy_df['macro_spread'].std()
    mean_past_90d, std_past_90d = valid_xy_df['past_90d_return'].mean(), valid_xy_df['past_90d_return'].std()
    
    current_data = valid_xy_df.iloc[-1]
    today_past_90d_change = (current_data['close'] - valid_xy_df.iloc[-91]['close']) / valid_xy_df.iloc[-91]['close'] * 100
    
    is_gc = current_data['MA_30'] > current_data['MA_365']
    is_up = current_data['ma365_slope'] > 0
    if is_gc and not is_up: today_season = "🌸 春"
    elif is_gc and is_up: today_season = "🌻 夏"
    elif not is_gc and is_up: today_season = "🍁 秋"
    else: today_season = "⛄ 冬"
    
    hist_df = valid_xy_df.dropna(subset=['future_90d_return']).copy()
    hist_df['z_macro'] = (hist_df['macro_spread'] - mean_macro) / std_macro
    hist_df['z_past_90d'] = (hist_df['past_90d_return'] - mean_past_90d) / std_past_90d
    current_z_macro = (current_data['macro_spread'] - mean_macro) / std_macro
    current_z_past_90d = (current_data['past_90d_return'] - mean_past_90d) / std_past_90d
    
    if is_gc and not is_up: mask = (hist_df['MA_30'] > hist_df['MA_365']) & (hist_df['ma365_slope'] <= 0)
    elif is_gc and is_up: mask = (hist_df['MA_30'] > hist_df['MA_365']) & (hist_df['ma365_slope'] > 0)
    elif not is_gc and is_up: mask = (hist_df['MA_30'] <= hist_df['MA_365']) & (hist_df['ma365_slope'] > 0)
    else: mask = (hist_df['MA_30'] <= hist_df['MA_365']) & (hist_df['ma365_slope'] <= 0)
    
    hist_season_df = hist_df[mask].copy()
    hist_season_df['dist'] = np.sqrt((hist_season_df['z_macro'] - current_z_macro)**2 + (hist_season_df['z_past_90d'] - current_z_past_90d)**2)
    top_7 = hist_season_df.nsmallest(7, 'dist')
    
    today_exp_90d = 0.0
    if len(top_7) > 0:
        weights = 1 / (top_7['dist']**2 + 1e-8)
        today_exp_90d = (top_7['future_90d_return'] * (weights / weights.sum())).sum()

    # 7日間予測 (乱数シミュ)
    recent_90d_returns = valid_xy_df['daily_return'].tail(90)
    mu, sigma = recent_90d_returns.mean(), recent_90d_returns.std()
    
    sim_paths = [current_data['close'] * np.cumprod(1 + np.random.normal(mu, sigma, 7)) for _ in range(100)]
    expected_prices_7d = np.mean(sim_paths, axis=0)
    
    future_dates = [current_data['date'] + datetime.timedelta(days=i) for i in range(1, 8)]
    future_df = pd.DataFrame({'date': future_dates, 'close': expected_prices_7d})
    
    temp_df = pd.concat([df[['date', 'close']], future_df], ignore_index=True)
    temp_df['MA_30'] = temp_df['close'].rolling(window=30).mean()
    temp_df['MA_365'] = temp_df['close'].rolling(window=365).mean()
    temp_df['MA_1460'] = temp_df['close'].rolling(window=1460).mean()
    temp_df['ma365_slope'] = temp_df['MA_365'].diff(5)
    temp_df['macro_spread'] = (temp_df['MA_365'] - temp_df['MA_1460']) / temp_df['MA_1460'] * 100
    temp_df['past_90d_change'] = temp_df['close'].pct_change(periods=90) * 100
    
    future_7d_data = temp_df.iloc[-7:].copy()
    
    # 週間予報データの作成
    forecast_results = []
    for i, row in future_7d_data.iterrows():
        f_gc = row['MA_30'] > row['MA_365']
        f_up = row['ma365_slope'] > 0
        if f_gc and not f_up: f_season = "🌸 春"
        elif f_gc and f_up: f_season = "🌻 夏"
        elif not f_gc and f_up: f_season = "🍁 秋"
        else: f_season = "⛄ 冬"
        
        if f_gc and not f_up: f_mask = (hist_df['MA_30'] > hist_df['MA_365']) & (hist_df['ma365_slope'] <= 0)
        elif f_gc and f_up: f_mask = (hist_df['MA_30'] > hist_df['MA_365']) & (hist_df['ma365_slope'] > 0)
        elif not f_gc and f_up: f_mask = (hist_df['MA_30'] <= hist_df['MA_365']) & (hist_df['ma365_slope'] > 0)
        else: f_mask = (hist_df['MA_30'] <= hist_df['MA_365']) & (hist_df['ma365_slope'] <= 0)
            
        hist_f_season_df = hist_df[f_mask].copy()
        row_z_macro = (row['macro_spread'] - mean_macro) / std_macro
        row_z_past_90d = (row['past_90d_change'] - mean_past_90d) / std_past_90d
        
        dists = np.sqrt((hist_f_season_df['z_macro'] - row_z_macro)**2 + (hist_f_season_df['z_past_90d'] - row_z_past_90d)**2)
        
        if len(dists) > 0:
            top_7_idx = dists.nsmallest(7).index
            top_7_f = hist_f_season_df.loc[top_7_idx].copy()
            top_7_f['dist'] = dists.loc[top_7_idx]
            weights = 1 / (top_7_f['dist']**2 + 1e-8)
            exp_90d = (top_7_f['future_90d_return'] * (weights / weights.sum())).sum()
        else:
            exp_90d = 0.0

        forecast_results.append({
            'date': row['date'], 'season': f_season, 
            'past_90d': row['past_90d_change'], 'future_90d': exp_90d
        })

    return {
        "today_season": today_season, "today_past_90d_change": today_past_90d_change, "today_exp_90d": today_exp_90d,
        "valid_xy_df": valid_xy_df, "current_data": current_data,
        "x_forecast": [current_data['macro_spread']] + future_7d_data['macro_spread'].tolist(),
        "y_forecast": [today_past_90d_change] + future_7d_data['past_90d_change'].tolist(),
        "sigma": sigma,
        "forecast_results": forecast_results # 追加！
    }