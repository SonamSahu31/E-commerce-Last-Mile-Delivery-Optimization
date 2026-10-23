"""
E-commerce Last-Mile Delivery Optimization - end-to-end pipeline.

Usage:  python src/pipeline.py
- If data/amazon_delivery.csv (Kaggle: sujalsuthar/amazon-delivery-dataset) exists it is used.
- Otherwise a synthetic dataset with the SAME schema is generated (reproducible, seed=42).
Outputs: images/*.png, data/cleaned_delivery_data.csv, data/results.json
"""
import json, os, warnings
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.model_selection import train_test_split, cross_val_score, RandomizedSearchCV
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.ensemble import RandomForestRegressor, HistGradientBoostingRegressor, RandomForestClassifier
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (mean_absolute_error, mean_squared_error, r2_score, accuracy_score,
                             f1_score, roc_auc_score, confusion_matrix, silhouette_score)

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA, IMG = f"{ROOT}/data", f"{ROOT}/images"
os.makedirs(IMG, exist_ok=True)
sns.set_theme(style="whitegrid")
rng = np.random.default_rng(42)
SLA_MIN = 120          # a delivery is "late" if it takes more than 120 minutes
RES = {}

# ------------------------------------------------------------------ 1. DATA
def haversine(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * np.arcsin(np.sqrt(a))

def make_synthetic(n=20000):
    cities = [(12.97, 77.59), (19.07, 72.87), (28.61, 77.20), (17.38, 78.48), (13.08, 80.27), (22.57, 88.36)]
    c = np.array(cities)[rng.integers(0, len(cities), n)]
    slat, slon = c[:, 0] + rng.normal(0, .03, n), c[:, 1] + rng.normal(0, .03, n)
    dist_true = rng.gamma(2.2, 3.2, n) + .5
    ang = rng.uniform(0, 2 * np.pi, n)
    dlat, dlon = slat + dist_true / 111 * np.sin(ang), slon + dist_true / 111 * np.cos(ang)
    date = pd.to_datetime("2022-02-11") + pd.to_timedelta(rng.integers(0, 60, n), unit="D")
    hour = rng.choice(np.arange(8, 23), n, p=np.array([2, 3, 4, 5, 6, 6, 5, 5, 6, 8, 9, 9, 7, 5, 3]) / 94 * 1.0 / (np.array([2,3,4,5,6,6,5,5,6,8,9,9,7,5,3]).sum() / 94))
    minute = rng.integers(0, 60, n)
    prep = rng.integers(5, 16, n)
    order_t = pd.to_datetime(date.astype(str) + " " + pd.Series(hour).astype(str) + ":" + pd.Series(minute).astype(str).str.zfill(2))
    pick_t = order_t + pd.to_timedelta(prep, unit="m")
    weather = rng.choice(["Sunny", "Cloudy", "Fog", "Windy", "Stormy", "Sandstorms"], n, p=[.2, .2, .17, .17, .13, .13])
    traffic = rng.choice(["Low", "Medium", "High", "Jam"], n, p=[.25, .3, .28, .17])
    vehicle = rng.choice(["motorcycle", "scooter", "van", "bicycle"], n, p=[.55, .3, .1, .05])
    area = rng.choice(["Urban", "Metropolitian", "Semi-Urban", "Other"], n, p=[.3, .5, .1, .1])
    age = rng.integers(20, 40, n); rating = np.clip(rng.normal(4.6, .35, n), 2.5, 5).round(1)
    category = rng.choice(["Electronics", "Clothing", "Grocery", "Food", "Books", "Home", "Toys", "Sports"], n)
    t = (14 + 6.2 * dist_true + prep
         + pd.Series(traffic).map({"Low": 0, "Medium": 12, "High": 28, "Jam": 48}).values
         + pd.Series(weather).map({"Sunny": 0, "Cloudy": 4, "Fog": 12, "Windy": 8, "Stormy": 22, "Sandstorms": 18}).values
         + pd.Series(vehicle).map({"motorcycle": 0, "scooter": 3, "van": -4, "bicycle": 14}).values
         + pd.Series(area).map({"Urban": 0, "Metropolitian": 10, "Semi-Urban": 6, "Other": 12}).values
         + ((hour >= 18) & (hour <= 21)) * 10 - (rating - 4.6) * 8 + rng.normal(0, 8, n))
    df = pd.DataFrame({
        "Order_ID": [f"ORD{i:06d}" for i in range(n)], "Agent_Age": age, "Agent_Rating": rating,
        "Store_Latitude": slat, "Store_Longitude": slon, "Drop_Latitude": dlat, "Drop_Longitude": dlon,
        "Order_Date": date.strftime("%Y-%m-%d"), "Order_Time": order_t.dt.strftime("%H:%M:%S"),
        "Pickup_Time": pick_t.dt.strftime("%H:%M:%S"), "Weather": weather, "Traffic": traffic,
        "Vehicle": vehicle, "Area": area, "Delivery_Time": np.clip(t, 10, None).round(),
        "Category": category})
    return df

def inject_dirt(df):
    """Mimic real-world data problems so cleaning is meaningful."""
    d = df.copy(); n = len(d)
    d.loc[rng.choice(n, int(n * .015), replace=False), "Agent_Rating"] = np.nan
    d.loc[rng.choice(n, int(n * .01), replace=False), "Weather"] = np.nan
    d.loc[rng.choice(n, int(n * .012), replace=False), "Pickup_Time"] = np.nan
    d.loc[rng.choice(n, 120, replace=False), ["Store_Latitude", "Store_Longitude"]] = 0.0   # invalid coords
    d.loc[rng.choice(n, 40, replace=False), "Agent_Age"] = rng.choice([15, 16, 17], 40)       # invalid age
    d.loc[rng.choice(n, 30, replace=False), "Agent_Rating"] = 6.0                           # invalid rating
    d.loc[rng.choice(n, 150, replace=False), "Delivery_Time"] *= rng.uniform(3, 6, 150)       # extreme delays
    d["Traffic"] = d["Traffic"].where(rng.random(n) > .02, d["Traffic"].str.upper() + " ")   # messy labels
    return pd.concat([d, d.sample(60, random_state=1)])                                      # duplicates

path = f"{DATA}/amazon_delivery.csv"
if os.path.exists(path):
    raw = pd.read_csv(path); RES["data_source"] = "Kaggle Amazon Delivery Dataset (real)"
else:
    raw = inject_dirt(make_synthetic()); RES["data_source"] = "Synthetic dataset with the Amazon Delivery schema"
    raw.to_csv(f"{DATA}/raw_delivery_data.csv", index=False)

RES["raw_rows"] = int(len(raw)); RES["raw_cols"] = int(raw.shape[1])
RES["missing"] = {k: int(v) for k, v in raw.isna().sum().items() if v > 0}
RES["duplicates"] = int(raw.duplicated().sum())

# ------------------------------------------------------------------ 2. CLEANING
df = raw.drop_duplicates().copy()
for col in ["Traffic", "Weather", "Vehicle", "Area", "Category"]:
    df[col] = df[col].astype("string").str.strip().str.title()
df["Weather"] = df["Weather"].fillna(df["Weather"].mode()[0])
df["Agent_Rating"] = df["Agent_Rating"].where(df["Agent_Rating"].between(1, 5))
df["Agent_Rating"] = df["Agent_Rating"].fillna(df["Agent_Rating"].median())
bad_age = ~df["Agent_Age"].between(18, 70); RES["invalid_age"] = int(bad_age.sum())
df.loc[bad_age, "Agent_Age"] = df["Agent_Age"].median()
bad_geo = (df[["Store_Latitude", "Store_Longitude", "Drop_Latitude", "Drop_Longitude"]].abs() < 1).any(axis=1)
RES["invalid_coords"] = int(bad_geo.sum()); df = df[~bad_geo]
ot = pd.to_datetime(df["Order_Date"] + " " + df["Order_Time"], errors="coerce")
pt = pd.to_datetime(df["Order_Date"] + " " + df["Pickup_Time"], errors="coerce")
df["prep_min"] = ((pt - ot).dt.total_seconds() / 60).clip(0, 60)
df["prep_min"] = df["prep_min"].fillna(df["prep_min"].median())
df["hour"] = ot.dt.hour; df["dow"] = ot.dt.dayofweek
df["distance_km"] = haversine(df.Store_Latitude, df.Store_Longitude, df.Drop_Latitude, df.Drop_Longitude)

def iqr_bounds(s, k=1.5):
    q1, q3 = s.quantile([.25, .75]); i = q3 - q1; return q1 - k * i, q3 + k * i
before_out = df.copy()
lo, hi = iqr_bounds(df["Delivery_Time"], 3.0)
out_mask = (df["Delivery_Time"] > hi) | (df["Delivery_Time"] < lo)
RES["outliers_removed"] = int(out_mask.sum()); df = df[~out_mask].reset_index(drop=True)
df["late"] = (df["Delivery_Time"] > SLA_MIN).astype(int)
RES["clean_rows"] = int(len(df))

num_cols = ["Agent_Age", "Agent_Rating", "distance_km", "prep_min"]
scaler = StandardScaler(); df_scaled = df.copy(); df_scaled[num_cols] = scaler.fit_transform(df[num_cols])
df.to_csv(f"{DATA}/cleaned_delivery_data.csv", index=False)

fig, ax = plt.subplots(1, 3, figsize=(17, 4.8))
sns.boxplot(y=raw["Delivery_Time"].dropna(), ax=ax[0], color="#e76f51"); ax[0].set_title("Delivery time BEFORE cleaning"); ax[0].set_ylabel("minutes")
sns.boxplot(y=df["Delivery_Time"], ax=ax[1], color="#2a9d8f"); ax[1].set_title("Delivery time AFTER cleaning"); ax[1].set_ylabel("minutes")
ms = pd.Series(RES["missing"]); ax[2].barh(ms.index, ms.values, color="#f4a261"); ax[2].set_title("Missing values found (count)")
plt.tight_layout(); plt.savefig(f"{IMG}/01_data_cleaning.png", dpi=120); plt.close()

# ------------------------------------------------------------------ 3. KPIs
RES["kpi"] = {"on_time_pct": round(100 * (1 - df.late.mean()), 1), "avg_delivery_min": round(df.Delivery_Time.mean(), 1),
              "median_delivery_min": round(df.Delivery_Time.median(), 1), "avg_distance_km": round(df.distance_km.mean(), 2),
              "avg_min_per_km": round((df.Delivery_Time / df.distance_km).median(), 1)}

# ------------------------------------------------------------------ 4. EDA
RES["eda"] = {"corr_distance_time": round(df[["distance_km", "Delivery_Time"]].corr().iloc[0, 1], 2),
              "mean": round(df.Delivery_Time.mean(), 1), "median": round(df.Delivery_Time.median(), 1),
              "std": round(df.Delivery_Time.std(), 1), "skew": round(df.Delivery_Time.skew(), 2),
              "time_by_traffic": df.groupby("Traffic").Delivery_Time.mean().round(1).to_dict(),
              "time_by_weather": df.groupby("Weather").Delivery_Time.mean().round(1).to_dict(),
              "time_by_vehicle": df.groupby("Vehicle").Delivery_Time.mean().round(1).to_dict(),
              "late_by_traffic": (df.groupby("Traffic").late.mean() * 100).round(1).to_dict(),
              "peak_hour_time": df.groupby("hour").Delivery_Time.mean().round(1).to_dict()}
order = ["Low", "Medium", "High", "Jam"]
fig, ax = plt.subplots(2, 2, figsize=(15, 10))
sns.histplot(df.Delivery_Time, bins=45, kde=True, ax=ax[0, 0], color="#264653")
ax[0, 0].axvline(df.Delivery_Time.mean(), color="red", ls="--", label=f"Mean {df.Delivery_Time.mean():.0f} min")
ax[0, 0].axvline(SLA_MIN, color="orange", ls=":", label=f"SLA {SLA_MIN} min"); ax[0, 0].legend(); ax[0, 0].set_title("Distribution of delivery time"); ax[0, 0].set_xlabel("minutes")
sns.boxplot(data=df, x="Traffic", y="Delivery_Time", order=order, ax=ax[0, 1], palette="YlOrRd"); ax[0, 1].set_title("Delivery time by traffic level"); ax[0, 1].set_ylabel("minutes")
sns.scatterplot(data=df.sample(2500, random_state=1), x="distance_km", y="Delivery_Time", hue="Traffic", hue_order=order, alpha=.5, ax=ax[1, 0], palette="YlOrRd")
ax[1, 0].set_title("Distance vs delivery time"); ax[1, 0].set_ylabel("minutes")
sns.barplot(data=df, x="Weather", y="Delivery_Time", ax=ax[1, 1], palette="mako", errorbar=None); ax[1, 1].set_title("Average delivery time by weather"); ax[1, 1].tick_params(axis="x", rotation=20); ax[1, 1].set_ylabel("minutes")
plt.tight_layout(); plt.savefig(f"{IMG}/02_eda_overview.png", dpi=120); plt.close()

fig, ax = plt.subplots(1, 3, figsize=(19, 5.5))
sns.heatmap(df[["Agent_Age", "Agent_Rating", "distance_km", "prep_min", "hour", "Delivery_Time"]].corr(), annot=True, fmt=".2f", cmap="coolwarm", center=0, ax=ax[0]); ax[0].set_title("Correlation heatmap")
hh = df.groupby("hour").Delivery_Time.mean(); ax[1].plot(hh.index, hh.values, marker="o", color="#d00000"); ax[1].set_title("Average delivery time by hour of day"); ax[1].set_xlabel("hour"); ax[1].set_ylabel("minutes")
pv = df.pivot_table(index="Area", columns="Vehicle", values="Delivery_Time", aggfunc="mean")
sns.heatmap(pv, annot=True, fmt=".0f", cmap="YlGnBu", ax=ax[2]); ax[2].set_title("Avg minutes: area x vehicle")
plt.tight_layout(); plt.savefig(f"{IMG}/03_correlation_trends.png", dpi=120); plt.close()

# ------------------------------------------------------------------ 5. CLUSTERING (zones)
Z = StandardScaler().fit_transform(df[["distance_km", "Delivery_Time", "prep_min"]])
sil = {}
for k in range(2, 7):
    sil[k] = silhouette_score(Z, KMeans(k, random_state=42, n_init=5).fit_predict(Z), sample_size=4000, random_state=1)
best_k = max(sil, key=sil.get)
km = KMeans(best_k, random_state=42, n_init=10).fit(Z); df["segment"] = km.labels_
seg = df.groupby("segment").agg(orders=("Order_ID", "size"), avg_km=("distance_km", "mean"), avg_min=("Delivery_Time", "mean"), late_pct=("late", lambda s: 100 * s.mean())).round(1)
RES["kmeans"] = {"best_k": int(best_k), "silhouette": {int(k): round(v, 3) for k, v in sil.items()}, "segments": seg.reset_index().to_dict("records")}
fig, ax = plt.subplots(1, 2, figsize=(14, 5))
ax[0].plot(list(sil), list(sil.values()), marker="o"); ax[0].axvline(best_k, color="red", ls="--"); ax[0].set_title("Silhouette score vs k"); ax[0].set_xlabel("k")
sns.scatterplot(data=df.sample(3000, random_state=1), x="distance_km", y="Delivery_Time", hue="segment", palette="tab10", alpha=.6, ax=ax[1]); ax[1].set_title(f"Delivery segments (K-Means, k={best_k})")
plt.tight_layout(); plt.savefig(f"{IMG}/04_clustering.png", dpi=120); plt.close()

# ------------------------------------------------------------------ 6. REGRESSION
cat = ["Weather", "Traffic", "Vehicle", "Area", "Category"]
feats = ["Agent_Age", "Agent_Rating", "distance_km", "prep_min", "hour", "dow"] + cat
X = pd.get_dummies(df[feats], columns=cat, drop_first=True).astype(float); y = df.Delivery_Time
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=.2, random_state=42)
models = {"Linear Regression": LinearRegression(),
          "Random Forest": RandomForestRegressor(150, max_depth=14, min_samples_leaf=5, random_state=42, n_jobs=-1),
          "Hist Gradient Boosting": HistGradientBoostingRegressor(random_state=42)}
rows, preds = [], {}
for n, m in models.items():
    m.fit(Xtr, ytr); p = m.predict(Xte); preds[n] = p
    rows.append([n, mean_absolute_error(yte, p), np.sqrt(mean_squared_error(yte, p)), r2_score(yte, p), cross_val_score(m, X, y, cv=3, scoring="r2", n_jobs=-1).mean()])
R = pd.DataFrame(rows, columns=["Model", "MAE", "RMSE", "R2", "CV_R2"]).round(3)
search = RandomizedSearchCV(HistGradientBoostingRegressor(random_state=42),
    {"learning_rate": [.03, .05, .1, .2], "max_depth": [3, 5, 7, None], "max_leaf_nodes": [15, 31, 63], "l2_regularization": [0, .1, 1]},
    n_iter=10, cv=3, scoring="neg_root_mean_squared_error", random_state=42, n_jobs=-1).fit(Xtr, ytr)
tuned = search.best_estimator_; pt_ = tuned.predict(Xte)
tuned_row = {"Model": "Tuned HistGB", "MAE": round(mean_absolute_error(yte, pt_), 3), "RMSE": round(float(np.sqrt(mean_squared_error(yte, pt_))), 3), "R2": round(r2_score(yte, pt_), 3)}
RES["regression"] = R.to_dict("records") + [tuned_row]; RES["best_params"] = {k: (v if v is None else float(v) if isinstance(v, float) else int(v)) for k, v in search.best_params_.items()}
imp = pd.Series(RandomForestRegressor(150, max_depth=14, min_samples_leaf=5, random_state=42, n_jobs=-1).fit(Xtr, ytr).feature_importances_, index=X.columns).sort_values(ascending=False)
RES["top_features"] = imp.head(6).round(3).to_dict()

fig, ax = plt.subplots(1, 3, figsize=(19, 5.5))
allm = R.copy(); allm.loc[len(allm)] = ["Tuned HistGB", tuned_row["MAE"], tuned_row["RMSE"], tuned_row["R2"], np.nan]
x = np.arange(len(allm)); ax[0].bar(x - .2, allm.MAE, .4, label="MAE", color="#2a9d8f"); ax[0].bar(x + .2, allm.RMSE, .4, label="RMSE", color="#e9c46a")
ax[0].set_xticks(x); ax[0].set_xticklabels(allm.Model, rotation=15); ax[0].legend(); ax[0].set_title("Error in minutes (lower is better)")
for i, r in allm.iterrows(): ax[0].text(i, r.RMSE + .15, f"R²={r.R2:.2f}", ha="center", fontsize=9)
ax[1].scatter(yte, pt_, s=6, alpha=.3, color="#264653"); lim = [yte.min(), yte.quantile(.999)]; ax[1].plot(lim, lim, "r--"); ax[1].set_xlim(lim); ax[1].set_ylim(lim)
ax[1].set_xlabel("Actual minutes"); ax[1].set_ylabel("Predicted minutes"); ax[1].set_title("Actual vs predicted (tuned model)")
t10 = imp.head(8)[::-1]; ax[2].barh(t10.index, t10.values, color="#6a4c93"); ax[2].set_title("Top drivers of delivery time")
plt.tight_layout(); plt.savefig(f"{IMG}/05_regression_results.png", dpi=120); plt.close()

# ------------------------------------------------------------------ 7. CLASSIFICATION (late delivery)
Xc_tr, Xc_te, yc_tr, yc_te = train_test_split(X, df.late, test_size=.2, random_state=42, stratify=df.late)
clf = RandomForestClassifier(200, max_depth=14, min_samples_leaf=5, class_weight="balanced", random_state=42, n_jobs=-1).fit(Xc_tr, yc_tr)
lg = LogisticRegression(max_iter=2000, class_weight="balanced").fit(StandardScaler().fit(Xc_tr).transform(Xc_tr), yc_tr)
pc = clf.predict(Xc_te); pp = clf.predict_proba(Xc_te)[:, 1]
RES["classification"] = {"accuracy": round(accuracy_score(yc_te, pc), 3), "f1": round(f1_score(yc_te, pc), 3), "roc_auc": round(roc_auc_score(yc_te, pp), 3),
                         "late_rate_pct": round(100 * df.late.mean(), 1),
                         "logit_auc": round(roc_auc_score(yc_te, lg.predict_proba(StandardScaler().fit(Xc_tr).transform(Xc_te))[:, 1]), 3)}
cm = confusion_matrix(yc_te, pc)

# ------------------------------------------------------------------ 8. OPTIMIZATION A: what-if vehicle/dispatch policy
te = df.loc[Xte.index].copy(); te["pred"] = pt_
hi_risk = te[(te.Traffic.isin(["High", "Jam"])) & (te.distance_km > 8)]
sw = te.copy(); idx = sw.index.intersection(hi_risk.index)
alt = X.loc[idx].copy()
base_pred = tuned.predict(X.loc[idx])
alt["prep_min"] = np.clip(alt["prep_min"] - 3, 0, None)          # scenario: 3 min faster order preparation
alt_pred = tuned.predict(alt)
RES["whatif"] = {"orders_affected": int(len(idx)), "avg_min_saved_prep": round(float((base_pred - alt_pred).mean()), 2)}

# ------------------------------------------------------------------ 9. OPTIMIZATION B: vehicle routing (OR-Tools)
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
c0 = df[(df.Store_Latitude.between(12.8, 13.2)) & (df.Store_Longitude.between(77.4, 77.8))]
depot = (c0.Store_Latitude.median(), c0.Store_Longitude.median())
orders = c0.sample(30, random_state=7)[["Drop_Latitude", "Drop_Longitude"]].reset_index(drop=True)
pts = np.vstack([[depot], orders.values]); N = len(pts)
D = np.array([[haversine(a[0], a[1], b[0], b[1]) for b in pts] for a in pts])
demand = [0] + list(rng.integers(1, 5, N - 1)); VEH, CAP = 4, 35
Dm = (D * 1000).astype(int)
mgr = pywrapcp.RoutingIndexManager(N, VEH, 0); rt = pywrapcp.RoutingModel(mgr)
cb = rt.RegisterTransitCallback(lambda i, j: Dm[mgr.IndexToNode(i)][mgr.IndexToNode(j)]); rt.SetArcCostEvaluatorOfAllVehicles(cb)
dcb = rt.RegisterUnaryTransitCallback(lambda i: demand[mgr.IndexToNode(i)]); rt.AddDimensionWithVehicleCapacity(dcb, 0, [CAP] * VEH, True, "Cap")
sp = pywrapcp.DefaultRoutingSearchParameters(); sp.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
sp.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH; sp.time_limit.seconds = 5
sol = rt.SolveWithParameters(sp); routes = []
for v in range(VEH):
    i = rt.Start(v); r = [0]
    while not rt.IsEnd(i): i = sol.Value(rt.NextVar(i)); r.append(mgr.IndexToNode(i))
    routes.append(r)
opt_km = sum(D[r[k], r[k + 1]] for r in routes for k in range(len(r) - 1))
# baseline: greedy nearest-neighbour dispatch with the same capacity (what a simple dispatcher would do)
left, base_routes = set(range(1, N)), []
while left:
    r, load, cur = [0], 0, 0
    while True:
        cand = [j for j in left if load + demand[j] <= CAP]
        if not cand: break
        j = min(cand, key=lambda z: D[cur][z]); r.append(j); left.remove(j); load += demand[j]; cur = j
    r.append(0); base_routes.append(r)
base_km = sum(D[r[k], r[k + 1]] for r in base_routes for k in range(len(r) - 1))
RES["vrp"] = {"stops": int(N - 1), "vehicles_used": int(sum(len(r) > 2 for r in routes)), "capacity": CAP,
              "baseline_km": round(float(base_km), 1), "optimized_km": round(float(opt_km), 1),
              "saving_pct": round(float(100 * (1 - opt_km / base_km)), 1)}
fig, ax = plt.subplots(1, 2, figsize=(15, 6.5))
for a, rs, ttl in [(ax[0], base_routes, f"Baseline (greedy nearest-neighbour): {base_km:.1f} km"), (ax[1], routes, f"OR-Tools optimized: {opt_km:.1f} km")]:
    for k, r in enumerate([r for r in rs if len(r) > 2]):
        a.plot(pts[r, 1], pts[r, 0], "-o", ms=4, lw=1.6, label=f"Rider {k + 1}")
    a.scatter(depot[1], depot[0], marker="*", s=300, color="black", zorder=5, label="Store"); a.set_title(ttl); a.legend(fontsize=8); a.set_xlabel("longitude"); a.set_ylabel("latitude")
plt.tight_layout(); plt.savefig(f"{IMG}/06_route_optimization.png", dpi=120); plt.close()

# ------------------------------------------------------------------ 10. Classification + impact charts
fig, ax = plt.subplots(1, 3, figsize=(18, 5))
sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", ax=ax[0], xticklabels=["On time", "Late"], yticklabels=["On time", "Late"]); ax[0].set_title("Late-delivery classifier: confusion matrix"); ax[0].set_xlabel("Predicted"); ax[0].set_ylabel("Actual")
lt = (df.groupby("Traffic").late.mean().reindex(order) * 100); ax[1].bar(lt.index, lt.values, color="#e76f51"); ax[1].set_title("Late deliveries % by traffic")
for i, v in enumerate(lt.values): ax[1].text(i, v + 1, f"{v:.0f}%", ha="center")
ax[2].bar(["Baseline", "Optimized"], [base_km, opt_km], color=["#e76f51", "#2a9d8f"]); ax[2].set_title("Route distance, 30 stops (km)")
for i, v in enumerate([base_km, opt_km]): ax[2].text(i, v + .5, f"{v:.1f}", ha="center")
plt.tight_layout(); plt.savefig(f"{IMG}/07_classification_impact.png", dpi=120); plt.close()

# ------------------------------------------------------------------ 11. Dashboard & roadmap
fig = plt.figure(figsize=(16, 7.5)); gs = fig.add_gridspec(2, 4)
k = RES["kpi"]; best = min(RES["regression"], key=lambda r: r["RMSE"])
for i, (t, v, col) in enumerate([("On-time delivery (<=120 min)", f"{k['on_time_pct']}%", "#2a9d8f"), ("Avg delivery time", f"{k['avg_delivery_min']:.0f} min", "#264653"),
                                 ("Best model MAE", f"{best['MAE']:.1f} min", "#6a4c93"), ("Route km saved (VRP)", f"{RES['vrp']['saving_pct']}%", "#e76f51")]):
    a = fig.add_subplot(gs[0, i]); a.set_facecolor(col); a.set_xticks([]); a.set_yticks([])
    a.text(.5, .6, v, ha="center", va="center", fontsize=26, color="white", weight="bold"); a.text(.5, .22, t, ha="center", fontsize=11, color="white")
a = fig.add_subplot(gs[1, :2]); vt = df.groupby("Vehicle").Delivery_Time.mean().sort_values(); a.barh(vt.index, vt.values, color="#8ecae6"); a.set_title("Avg delivery minutes by vehicle")
a = fig.add_subplot(gs[1, 2:]); a.plot(hh.index, hh.values, marker="o", color="#d00000"); a.set_title("Avg delivery minutes by hour"); a.set_xlabel("hour")
fig.suptitle("E-commerce Last-Mile Delivery Optimization - KPI dashboard", fontsize=16, weight="bold"); plt.tight_layout(rect=[0, 0, 1, .95]); plt.savefig(f"{IMG}/00_kpi_dashboard.png", dpi=120); plt.close()

fig, ax = plt.subplots(figsize=(16, 3.6)); ax.axis("off")
steps = [("1. Data\ncollection", "#264653"), ("2. Cleaning &\npreprocessing", "#2a9d8f"), ("3. EDA &\nvisualization", "#e9c46a"), ("4. Predictive\nmodeling", "#f4a261"), ("5. Route\noptimization", "#e76f51"), ("6. Evaluation &\ndecisions", "#6a4c93")]
for i, (t, col) in enumerate(steps):
    ax.add_patch(plt.Rectangle((i * 2.65, 0.2), 2.2, 1.6, color=col)); ax.text(i * 2.65 + 1.1, 1.0, t, ha="center", va="center", color="white" if col != "#e9c46a" else "black", fontsize=12, weight="bold")
    if i < len(steps) - 1: ax.annotate("", xy=(i * 2.65 + 2.65, 1.0), xytext=(i * 2.65 + 2.2, 1.0), annotation_clip=False, arrowprops=dict(arrowstyle="-|>", lw=2, color="black", mutation_scale=18))
ax.set_xlim(-.2, 16); ax.set_ylim(0, 2); ax.set_title("Strategic roadmap: end-to-end analysis flow", fontsize=14, weight="bold")
plt.savefig(f"{IMG}/08_roadmap.png", dpi=120, bbox_inches="tight"); plt.close()

json.dump(RES, open(f"{DATA}/results.json", "w"), indent=2, default=str)
print(json.dumps(RES, indent=1, default=str)[:4500])
