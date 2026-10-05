### FedAvg vs. Trimmed Mean

| Metric | FedAvg | Trimmed Mean |
|---|---:|---:|
| Loss variance | 476.5188 | **0.0232** |
| Update norm | 4.9254 | **1.0993** |
| Test accuracy | 0.4692 | 0.4692 |

Trimmed Mean produced much more stable model updates than standard FedAvg. The loss variance dropped from **476.5188 to 0.0232**, while the update norm decreased from **4.9254 to 1.0993**. Test accuracy remained unchanged at **46.92%**, indicating that the main benefit in this run was improved training stability rather than higher predictive accuracy.