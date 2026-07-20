from io import BytesIO
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from app.services.rag import (
    _basic_answer,
    _faq_match_score,
    _faq_payload,
    _wants_human,
)
from app.services.knowledge import search_terms
from app.services.retail_import import (
    COLUMN_ALIASES,
    REQUIRED_FIELDS,
    RetailImportError,
    RetailImportService,
    _normalize_rows,
)
from app.services.tabular import TabularDataError, read_tabular, remap_columns


ROOT = Path(__file__).resolve().parents[1]


def test_csv_reader_maps_chinese_headers_and_preserves_identifiers():
    data = (
        "客户编号,客户姓名,订单编号,下单时间,商品SKU,商品名称,数量,单价\n"
        "00001,张三,SO-001,2026-07-14 10:30:00,SKU-01,咖啡,2,29.90\n"
    ).encode("utf-8-sig")

    dataset = read_tabular("records.csv", data)
    rows = remap_columns(dataset, COLUMN_ALIASES, REQUIRED_FIELDS)

    assert rows[0]["customer_no"] == "00001"
    assert rows[0]["product_sku"] == "SKU-01"


def test_retail_rows_group_order_lines_and_reject_conflicting_customer():
    rows = (
        {
            "customer_no": "C1", "customer_name": "张三", "order_no": "SO1",
            "order_time": "2026-07-14 10:30:00", "product_sku": "SKU1",
            "product_name": "咖啡", "quantity": "1", "unit_price": "10",
        },
        {
            "customer_no": "C1", "customer_name": "张三", "order_no": "SO1",
            "order_time": "2026-07-14 10:30:00", "product_sku": "SKU1",
            "product_name": "咖啡", "quantity": "2", "unit_price": "10",
        },
    )
    _, _, orders = _normalize_rows(rows)
    assert orders["SO1"]["items"]["SKU1"]["quantity"] == 3
    assert str(orders["SO1"]["items"]["SKU1"]["line_amount"]) == "30.00"

    bad = ({**rows[0]}, {**rows[1], "customer_name": "李四"})
    with pytest.raises(RetailImportError, match="不同姓名"):
        _normalize_rows(bad)

    with pytest.raises(RetailImportError, match="数量必须是正整数"):
        _normalize_rows(({**rows[0], "quantity": "NaN"},))
    with pytest.raises(RetailImportError, match="单价格式不正确"):
        _normalize_rows(({**rows[0], "unit_price": "Infinity"},))
    with pytest.raises(RetailImportError, match="累计数量"):
        _normalize_rows((
            {**rows[0], "quantity": "600000"},
            {**rows[1], "quantity": "600000"},
        ))


def test_import_analysis_collects_multiple_independent_row_issues():
    payload = (
        "客户编号,客户姓名,订单编号,下单时间,商品SKU,商品名称,数量,单价\n"
        "C1,张三,SO1,not-a-date,SKU1,咖啡,0,not-a-price\n"
    ).encode("utf-8-sig")

    result = RetailImportService.analyze_dataset("bad.csv", payload)

    assert result["input_row_count"] == 1
    assert result["valid_row_count"] == 0
    assert result["invalid_row_count"] == 1
    assert {(issue["field_name"], issue["issue_code"]) for issue in result["issues"]} == {
        ("order_time", "invalid_datetime"),
        ("quantity", "invalid_quantity"),
        ("unit_price", "invalid_money"),
    }


def test_import_analysis_accepts_explicit_field_mapping():
    payload = (
        "编号,姓名,订单,时间,SKU,商品,数量,价格\n"
        "C1,张三,SO1,2026-07-20 10:00:00,SKU1,咖啡,1,9.90\n"
    ).encode("utf-8-sig")

    result = RetailImportService.analyze_dataset(
        "mapped.csv",
        payload,
        {
            "编号": "customer_no",
            "姓名": "customer_name",
            "订单": "order_no",
            "时间": "order_time",
            "SKU": "product_sku",
            "商品": "product_name",
            "数量": "quantity",
            "价格": "unit_price",
        },
    )

    assert result["valid_row_count"] == 1
    assert result["invalid_row_count"] == 0
    assert result["issues"] == []
    assert result["mapping"]["编号"] == "customer_no"


def test_xlsx_reader_and_invalid_spreadsheet_detection():
    output = BytesIO()
    pd.DataFrame([{"问题": "如何退款？", "答案": "七天内申请"}]).to_excel(
        output, index=False, engine="openpyxl"
    )
    dataset = read_tabular("faq.xlsx", output.getvalue())
    assert dataset.rows[0]["问题"] == "如何退款？"

    with pytest.raises(TabularDataError, match="有效 XLSX"):
        read_tabular("fake.xlsx", b"not-an-xlsx")

    oversized = BytesIO()
    manifest = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Override PartName="/xl/renamed-data.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
    </Types>'''
    with zipfile.ZipFile(oversized, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", manifest)
        archive.writestr("xl/renamed-data.xml", b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(TabularDataError, match="内容过大"):
        read_tabular("oversized.xlsx", oversized.getvalue())


def test_basic_answers_and_structured_faq_payload():
    assert "智能客服" in _basic_answer("你好")
    assert _wants_human("怎么联系人工客服？") is True
    assert _wants_human("不需要转人工") is False
    assert _wants_human("人工客服说订单已退款，多久到账？") is False
    assert _wants_human("联系人工后如何查工单") is False
    assert _wants_human("我要投诉，请转人工客服处理订单") is True
    assert _wants_human("麻烦人工客服帮我查退款") is True
    assert _wants_human("之前不需要转人工，现在请转人工客服") is True
    assert _wants_human("刚才说不要转人工，但现在我要人工客服") is True
    assert _basic_answer("请帮助我申请退款") is None
    assert "补充" in _basic_answer("退款")
    assert _basic_answer("订单什么时候发货") is None
    payload = _faq_payload('{"_type":"faq-v1","question":"如何退款","answer":"七天内申请"}')
    assert payload["answer"] == "七天内申请"
    assert _faq_payload("普通文档") is None
    query = "我想知道办理退费的期限有多久"
    assert _faq_match_score(
        query, "订单支付后多久可以申请退款？", search_terms("我想知道办理退款的期限有多久")
    ) >= 0.35
    assert _faq_match_score("退款", "订单支付后多久可以申请退款？", search_terms("退款")) == 0
    assert _faq_match_score(
        "订单支付失败怎么办", "订单支付后多久可以申请退款？",
        search_terms("订单支付失败怎么办"),
    ) == 0
    assert _faq_match_score(
        "订单支付状态怎么查", "订单支付后多久可以申请退款？",
        search_terms("订单支付状态怎么查"),
    ) == 0
    for unrelated in ("订单怎么办", "订单怎么取消", "退款怎么办", "商品退款不了怎么办"):
        assert _faq_match_score(
            unrelated, "订单支付后多久可以申请退款？", search_terms(unrelated)
        ) == 0


def test_routes_and_templates_expose_dataset_workflows():
    from app import create_app

    endpoints = {rule.endpoint for rule in create_app().url_map.iter_rules()}
    assert {
        "imports.download_template", "imports.upload_dataset", "imports.preview_dataset",
        "imports.confirm_dataset", "imports.download_error_report", "imports.batch_detail",
        "knowledge.download_dataset_template", "knowledge.upload_dataset",
    } <= endpoints
    imports = (ROOT / "app/templates/imports.html").read_text(encoding="utf-8")
    import_detail = (ROOT / "app/templates/import_batch_detail.html").read_text(encoding="utf-8")
    knowledge = (ROOT / "app/templates/knowledge/index.html").read_text(encoding="utf-8")
    qa = (ROOT / "app/templates/qa/chat.html").read_text(encoding="utf-8")
    assert "开始预检" in imports and ".csv,.xlsx" in imports
    assert "确认导入" in import_detail and "下载错误报告" in import_detail
    assert "导入问答数据集" in knowledge and "publish_now" in knowledge
    assert "data-question" in qa and "qa-question" in qa
