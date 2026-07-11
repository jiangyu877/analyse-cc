import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gradio_app
import matplotlib.pyplot as plt


def main():
    output_dir = ROOT / ".cache" / "chart-previews"
    output_dir.mkdir(parents=True, exist_ok=True)

    rfm_value, rfm_structure, rfm_table, rfm_text = gradio_app.rfm_analysis()
    rfm_value.savefig(output_dir / "rfm-value.png", dpi=120, bbox_inches="tight")
    rfm_structure.savefig(output_dir / "rfm-structure.png", dpi=120, bbox_inches="tight")
    print(f"rfm rows={len(rfm_table)} explanation={len(rfm_text)}")

    cluster_plot, cluster_table, cluster_text = gradio_app.user_segmentation()
    cluster_plot.savefig(output_dir / "kmeans.png", dpi=120, bbox_inches="tight")
    print(f"kmeans rows={len(cluster_table)} explanation={len(cluster_text)}")

    churn_top, churn_distribution, churn_table, churn_text = gradio_app.churn_prediction()
    churn_top.savefig(output_dir / "churn-top20.png", dpi=120, bbox_inches="tight")
    churn_distribution.savefig(output_dir / "churn-distribution.png", dpi=120, bbox_inches="tight")
    print(f"churn rows={len(churn_table)} explanation={len(churn_text)}")

    plt.close("all")
    print(output_dir)


if __name__ == "__main__":
    main()
