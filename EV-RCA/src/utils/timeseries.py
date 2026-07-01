import pandas as pd


def drop_constant(df: pd.DataFrame):
    return df.loc[:, (df != df.iloc[0]).any()]


def drop_near_constant(df: pd.DataFrame, threshold: float = 0.1):
    return df.loc[:, (df != df.iloc[0]).mean() > threshold]


def drop_time(df: pd.DataFrame):
    if "time" in df:
        df = df.drop(columns=["time"])
    elif "Time" in df:
        df = df.drop(columns=["Time"])
    return df


def drop_extra(df: pd.DataFrame):
    if "time.1" in df:
        df = df.drop(columns=["time.1"])

    # remove cols has "frontend-external" in name
    # remove cols start with "main_" or "PassthroughCluster_", etc.
    for col in df.columns:
        if (
            "frontend-external" in col
            or col.startswith("main_")
            or col.startswith("PassthroughCluster_")
            or col.startswith("redis_")
            or col.startswith("rabbitmq")
            or col.startswith("queue")
            or col.startswith("session")
            or col.startswith("istio-proxy")
        ):
            df = df.drop(columns=[col])

    return df


def convert_mem_mb(df: pd.DataFrame):
    # Convert memory to MBs
    def update_mem(x):
        if not x.name.endswith("_mem"):
            return x
        x /= 1e6
        # x = x.astype(int)
        return x

    return df.apply(update_mem)


def preprocess_sock_shop(df: pd.DataFrame):
    df = convert_mem_mb(drop_near_constant(drop_constant(drop_time(df))))

    # drop columns that endswith lat_50 and lat_99 column if exists
    for col in df.columns:
        if col.endswith("lat_50") or col.endswith("lat_99"):
            df = df.drop(columns=[col])

    return df


def select_useful_cols(data):
    selected_cols = []
    for c in data.columns:
        # keep time
        if "time" in c:
            selected_cols.append(c)

        # cpu
        if c.endswith("_cpu") and data[c].std() > 1:
            selected_cols.append(c)

        # mem
        if c.endswith("_mem") and data[c].std() > 1:
            selected_cols.append(c)

        # latency
        # if ("lat50" in c or "latency" in c) and (data[c] * 1000).std() > 10:
        if "lat50" in c and (data[c] * 1000).std() > 10:
            selected_cols.append(c)
    return selected_cols


def normalize_ts(data: pd.DataFrame):
    # minus mean and divide std for metrics, except time
    for c in data.columns:
        if c == "time":
            continue
        data[c] = (data[c] - data[c].mean()) / data[c].std()
    return data


def preprocess(data, dataset=None, dk_select_useful=False):
    before_cols_num = len(data.columns)

    if dataset == "causalrca-sock-shop":
        data = drop_time(data)

    elif dataset is not None:
        data = drop_time(data) #drop_constant(drop_time(data))
        data = convert_mem_mb(data)

        if dk_select_useful is True:
            data = drop_extra(data)
            data = drop_near_constant(data)
            data = data[select_useful_cols(data)]

    # -------------------------
    # NaN handling
    # -------------------------
    nan_count = data.isna().sum().sum()

    if nan_count > 0:
        print(f"[WARNING] Found {nan_count} NaN values in preprocessed data")

        cols_with_nan = data.columns[data.isna().any()]

        for col in cols_with_nan:
            print(
                f"  {col}: "
                f"{data[col].isna().sum()} NaNs"
            )

        data = data.fillna(0)

        print("[INFO] Replaced NaNs with 0")

    after_cols_num = len(data.columns)
    
    return data


def preprocess_testdata(data, columns_to_keep):
    data = drop_time(data)
    data = data[columns_to_keep]    
    data = convert_mem_mb(data)


    # -------------------------
    # NaN handling
    # -------------------------
    nan_count = data.isna().sum().sum()

    if nan_count > 0:
        print(f"[WARNING] Found {nan_count} NaN values in preprocessed data")

        cols_with_nan = data.columns[data.isna().any()]

        for col in cols_with_nan:
            print(
                f"  {col}: "
                f"{data[col].isna().sum()} NaNs"
            )

        data = data.fillna(0)

        print("[INFO] Replaced NaNs with 0")

    after_cols_num = len(data.columns)
    
    return data

