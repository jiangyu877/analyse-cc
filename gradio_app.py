import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent / ".cache" / "matplotlib"))

import gradio as gr
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app import create_app
from app.services.algorithms import run_churn, run_kmeans, run_rfm
from app.services.prediction import (
    PredictionService,
    load_customer_amount_predictions,
    load_product_recommendations,
    load_product_sales_forecasts,
)
from app.services.workbench import (
    load_churn_result,
    load_cluster_result,
    load_rfm_result,
)


BG = "#111318"
PANEL = "#191c22"
FG = "#f4f5f7"
MUTED = "#a6abb5"
GREEN = "#35c98b"
BLUE = "#5b8def"
ORANGE = "#e6a23c"
RED = "#d1495b"
PURPLE = "#9b6ef3"
PALETTE = [GREEN, BLUE, PURPLE, ORANGE, RED, "#37b7c3", "#d46a92", "#8a9a5b"]

flask_app = create_app()


def _style_figure(fig, axes):
    fig.patch.set_facecolor(BG)
    axes = np.atleast_1d(axes)
    for ax in axes:
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=MUTED)
        ax.xaxis.label.set_color(MUTED)
        ax.yaxis.label.set_color(MUTED)
        ax.title.set_color(FG)
        for spine in ax.spines.values():
            spine.set_color("#343943")
        ax.grid(color="#343943", alpha=0.35, linewidth=0.6)
    fig.tight_layout()
    return fig


def _empty_figure(message):
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.text(0.5, 0.5, message, ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])
    return _style_figure(fig, ax)


def _customer_label(frame):
    return frame["customer_no"].astype(str) + " · " + frame["name"].astype(str)


def run_rfm_view():
    columns = ["客户", "最近消费(天)", "消费频次", "累计净消费", "客户分层"]
    try:
        with flask_app.app_context():
            task_id = run_rfm(None)
            frame = pd.DataFrame([dict(row) for row in load_rfm_result(task_id)])
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:300]
        return _empty_figure("RFM任务执行失败"), _empty_figure("暂无分层数据"), pd.DataFrame(columns=columns), f"### 执行失败\n{message}"
    if frame.empty:
        return _empty_figure("没有可分析客户"), _empty_figure("暂无分层数据"), pd.DataFrame(columns=columns), "### 暂无结果"

    frame["customer"] = _customer_label(frame)
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for index, (segment, group) in enumerate(frame.groupby("segment", sort=True)):
        ax.scatter(
            group["recency_days"], group["monetary"],
            s=np.clip(group["frequency"] * 5 + 18, 20, 140),
            color=PALETTE[index % len(PALETTE)], alpha=0.68, label=f"{segment} ({len(group)})",
        )
    ax.invert_xaxis()
    ax.set_xlabel("最近消费距今天数")
    ax.set_ylabel("累计净消费金额")
    ax.set_title("客户价值地图")
    ax.legend(facecolor=PANEL, edgecolor="#343943", labelcolor=FG, fontsize=8)
    _style_figure(fig, ax)

    counts = frame["segment"].value_counts().sort_values()
    fig_bar, ax_bar = plt.subplots(figsize=(10, 4.8))
    ax_bar.barh(counts.index, counts.values, color=PALETTE[: len(counts)])
    ax_bar.set_xlabel("客户数量")
    ax_bar.set_title("客户分层结构")
    _style_figure(fig_bar, ax_bar)

    table = frame[["customer", "recency_days", "frequency", "monetary", "segment"]].head(500).copy()
    table.columns = columns
    table["累计净消费"] = table["累计净消费"].round(2)
    insight = (
        f"### RFM任务 #{task_id}\n"
        f"共分析 **{len(frame):,}** 位客户，形成 **{len(counts)}** 个客户层级。"
        "图表和明细均来自同一个任务结果。"
    )
    return fig, fig_bar, table, insight


def run_cluster_view(clusters):
    columns = ["客户群", "客户数", "平均最近消费", "平均频次", "平均净消费"]
    try:
        with flask_app.app_context():
            run_rfm(None)
            task_id = run_kmeans(None, int(clusters))
            frame = pd.DataFrame([dict(row) for row in load_cluster_result(task_id)])
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:300]
        return _empty_figure("KMeans任务执行失败"), pd.DataFrame(columns=columns), f"### 执行失败\n{message}"
    if frame.empty:
        return _empty_figure("没有聚类结果"), pd.DataFrame(columns=columns), "### 暂无结果"

    frame["customer"] = _customer_label(frame)
    fig, ax = plt.subplots(figsize=(11, 5.8))
    for label, group in frame.groupby("cluster_label", sort=True):
        ax.scatter(
            group["frequency"], group["monetary"],
            s=np.clip(24 + group["recency_days"], 25, 150),
            color=PALETTE[int(label) % len(PALETTE)], alpha=0.65,
            label=f"客户群 {int(label) + 1} ({len(group)})",
        )
    ax.set_xlabel("消费频次")
    ax.set_ylabel("累计净消费金额")
    ax.set_title("客户群位置与特征热力图")
    ax.legend(facecolor=PANEL, edgecolor="#343943", labelcolor=FG, fontsize=8)
    _style_figure(fig, ax)

    summary = frame.groupby("cluster_label").agg(
        customer_count=("customer_id", "count"),
        avg_recency=("recency_days", "mean"),
        avg_frequency=("frequency", "mean"),
        avg_monetary=("monetary", "mean"),
    ).reset_index()
    summary["cluster_label"] = summary["cluster_label"].map(lambda value: f"客户群 {int(value) + 1}")
    summary.columns = columns
    for column in columns[2:]:
        summary[column] = summary[column].round(2)
    insight = f"### KMeans任务 #{task_id}\n系统按RFM特征形成 **{len(summary)}** 个客户群。"
    return fig, summary, insight


def run_churn_view(observation_days):
    columns = ["客户", "流失概率", "风险等级", "最近消费(天)", "消费频次", "累计净消费"]
    try:
        with flask_app.app_context():
            run_rfm(None)
            task_id = run_churn(None, int(observation_days))
            rows, metrics = load_churn_result(task_id)
            frame = pd.DataFrame([dict(row) for row in rows])
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:300]
        return _empty_figure("流失任务执行失败"), _empty_figure("暂无风险分布"), pd.DataFrame(columns=columns), f"### 执行失败\n{message}"
    if frame.empty:
        return _empty_figure("没有流失结果"), _empty_figure("暂无风险分布"), pd.DataFrame(columns=columns), "### 暂无结果"

    frame["customer"] = _customer_label(frame)
    frame["risk"] = pd.cut(
        frame["churn_probability"], bins=[-0.01, 0.4, 0.7, 1.0],
        labels=["低风险", "需关注", "高风险"],
    )
    top = frame.head(20).sort_values("churn_probability")
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = top["risk"].map({"高风险": RED, "需关注": ORANGE, "低风险": GREEN})
    ax.barh(top["customer"], top["churn_probability"] * 100, color=colors)
    ax.set_xlabel("流失概率 (%)")
    ax.set_title("高风险客户 Top20")
    _style_figure(fig, ax)

    counts = frame["risk"].value_counts().reindex(["高风险", "需关注", "低风险"], fill_value=0)
    fig_pie, ax_pie = plt.subplots(figsize=(8, 5.5))
    ax_pie.pie(counts.values, labels=counts.index, autopct="%1.1f%%", colors=[RED, ORANGE, GREEN], textprops={"color": FG})
    ax_pie.set_title("风险结构与概率分布")
    _style_figure(fig_pie, ax_pie)

    table = frame[["customer", "churn_probability", "risk", "recency_days", "frequency", "monetary"]].head(500).copy()
    table["churn_probability"] = (table["churn_probability"] * 100).round(2).map(lambda value: f"{value:.2f}%")
    table.columns = columns
    auc = metrics.get("auc:training", 0)
    f1 = metrics.get("f1:training", 0)
    insight = f"### 流失任务 #{task_id}\nAUC **{auc:.3f}**，F1 **{f1:.3f}**，高风险客户 **{int(counts['高风险'])}** 位。"
    return fig, fig_pie, table, insight


def run_customer_amount_view():
    columns = ["客户", "预测开始", "预测结束", "未来30天预测金额"]
    try:
        with flask_app.app_context():
            task_id = PredictionService.run_customer_amount(
                None, horizon_days=30, training_days=180
            )
            frame = pd.DataFrame(load_customer_amount_predictions(task_id))
    except Exception as exc:
        return pd.DataFrame(columns=columns), f"### 执行失败\n{str(exc)[:300]}"
    if frame.empty:
        return pd.DataFrame(columns=columns), "### 暂无预测结果"
    frame["customer"] = _customer_label(frame)
    table = frame[["customer", "forecast_start", "forecast_end", "predicted_amount"]].copy()
    table.columns = columns
    table["未来30天预测金额"] = table["未来30天预测金额"].round(2)
    return table, f"### 客户消费额任务 #{task_id}\n共生成 **{len(table)}** 位客户的任务级预测。"


def run_product_sales_forecast_view():
    columns = ["商品", "未来30天预测销量"]
    try:
        with flask_app.app_context():
            task_id = PredictionService.run_product_sales_forecast(
                None, horizon_days=30, training_days=90
            )
            frame = pd.DataFrame(load_product_sales_forecasts(task_id))
    except Exception as exc:
        return pd.DataFrame(columns=columns), f"### 执行失败\n{str(exc)[:300]}"
    if frame.empty:
        return pd.DataFrame(columns=columns), "### 暂无预测结果"
    table = frame.groupby(["sku", "product_name"], as_index=False)["predicted_quantity"].sum()
    table["商品"] = table["sku"] + " · " + table["product_name"]
    table = table[["商品", "predicted_quantity"]]
    table.columns = columns
    table["未来30天预测销量"] = table["未来30天预测销量"].round(2)
    return table, f"### 商品销量任务 #{task_id}\n结果来自同一任务的逐日预测明细。"


def run_product_recommendation_view(top_k):
    columns = ["客户", "排名", "推荐商品", "相似度"]
    try:
        with flask_app.app_context():
            task_id = PredictionService.run_product_recommendation(
                None, top_k=int(top_k), training_days=180
            )
            frame = pd.DataFrame(load_product_recommendations(task_id))
    except Exception as exc:
        return pd.DataFrame(columns=columns), f"### 执行失败\n{str(exc)[:300]}"
    if frame.empty:
        return pd.DataFrame(columns=columns), "### 暂无推荐结果"
    frame["customer"] = _customer_label(frame)
    frame["product"] = frame["sku"] + " · " + frame["product_name"]
    table = frame[["customer", "rank_no", "product", "score"]].copy()
    table.columns = columns
    table["相似度"] = table["相似度"].round(4)
    return table, f"### 商品推荐任务 #{task_id}\n共生成 **{len(table)}** 条任务级推荐。"


with gr.Blocks(title="消费分析预测工作台") as demo:
    gr.Markdown("# 消费分析预测工作台\nRFM、客户分群和流失风险结果均按任务编号留存。")
    with gr.Tabs():
        with gr.TabItem("RFM客户价值"):
            run_rfm_button = gr.Button("执行RFM分析", variant="primary")
            with gr.Row():
                rfm_map = gr.Plot(label="客户价值地图")
                rfm_structure = gr.Plot(label="客户分层结构")
            rfm_table = gr.Dataframe(label="高优先级客户", interactive=False)
            rfm_insight = gr.Markdown()
            run_rfm_button.click(run_rfm_view, outputs=[rfm_map, rfm_structure, rfm_table, rfm_insight])

        with gr.TabItem("KMeans客户分群"):
            cluster_count = gr.Slider(2, 8, value=4, step=1, label="客户群数量")
            run_cluster_button = gr.Button("执行客户分群", variant="primary")
            cluster_plot = gr.Plot(label="客户群位置与特征热力图")
            cluster_table = gr.Dataframe(label="客户群业务画像", interactive=False)
            cluster_insight = gr.Markdown()
            run_cluster_button.click(run_cluster_view, inputs=[cluster_count], outputs=[cluster_plot, cluster_table, cluster_insight])

        with gr.TabItem("客户流失风险"):
            observation_days = gr.Slider(30, 180, value=90, step=10, label="观察窗口(天)")
            run_churn_button = gr.Button("执行流失预测", variant="primary")
            with gr.Row():
                churn_top = gr.Plot(label="高风险客户 Top20")
                churn_structure = gr.Plot(label="风险结构与概率分布")
            churn_table = gr.Dataframe(label="流失风险明细", interactive=False)
            churn_insight = gr.Markdown("模型指标包含 AUC 和 F1。")
            run_churn_button.click(
                run_churn_view,
                inputs=[observation_days],
                outputs=[churn_top, churn_structure, churn_table, churn_insight],
            )

        with gr.TabItem("30天客户消费额"):
            run_amount_button = gr.Button("执行客户消费额预测", variant="primary")
            amount_table = gr.Dataframe(label="客户预测明细", interactive=False)
            amount_insight = gr.Markdown()
            run_amount_button.click(
                run_customer_amount_view,
                outputs=[amount_table, amount_insight],
            )

        with gr.TabItem("商品销量预测"):
            run_sales_button = gr.Button("执行商品销量预测", variant="primary")
            sales_table = gr.Dataframe(label="商品预测汇总", interactive=False)
            sales_insight = gr.Markdown()
            run_sales_button.click(
                run_product_sales_forecast_view,
                outputs=[sales_table, sales_insight],
            )

        with gr.TabItem("商品智能推荐"):
            recommendation_count = gr.Slider(1, 20, value=5, step=1, label="每位客户推荐条数")
            run_recommendation_button = gr.Button("生成商品推荐", variant="primary")
            recommendation_table = gr.Dataframe(label="推荐明细", interactive=False)
            recommendation_insight = gr.Markdown()
            run_recommendation_button.click(
                run_product_recommendation_view,
                inputs=[recommendation_count],
                outputs=[recommendation_table, recommendation_insight],
            )


if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_PORT", "7860"))
    demo.launch(server_name='127.0.0.1', server_port=port, share=False, quiet=True)
