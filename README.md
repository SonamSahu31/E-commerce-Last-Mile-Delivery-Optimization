# E-commerce-Last-Mile-Delivery-Optimization

End-to-end data-science project on last-mile delivery: data cleaning, EDA, delivery-time prediction,
late-delivery classification, K-Means segmentation and vehicle-route optimization (Google OR-Tools).

## Results (cleaned data, 19,722 orders)
| Item | Result |
|---|---|  
| On-time delivery (<= 120 min) | 64.2% |
| Average delivery time | 111.4 min |
| Best regression model (tuned HistGradientBoosting) | MAE 7.1 min, R2 0.922 |
| Late-delivery classifier | ROC-AUC 0.974, F1 0.879 |
| Route optimization (30 stops, OR-Tools vs greedy) | 174.0 km -> 117.6 km (32.4% less) |

## Data
Reference dataset: Kaggle "Amazon Delivery Dataset" (`sujalsuthar/amazon-delivery-dataset`).
Put the CSV at `data/amazon_delivery.csv` to run on the real data. If the file is absent, the pipeline
generates a synthetic dataset with the same schema (seed 42), so all results are reproducible.
The numbers above come from the synthetic dataset.

## Run
```bash
pip install -r requirements.txt
python src/pipeline.py        # cleaning, EDA, models, routing -> images/, data/results.json
python src/build_reports.py   # builds the four Word reports in reports/
```

## Structure
- `src/pipeline.py` - full analysis pipeline
- `src/build_reports.py` - generates the weekly .docx reports
- `images/` - all charts
- `reports/` - Week 1-4 Word reports
- `data/` - raw/cleaned data and results.json
