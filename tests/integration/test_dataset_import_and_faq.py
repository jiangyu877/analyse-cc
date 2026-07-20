import pytest
from flask import session
from sqlalchemy import text


def test_dataset_and_qa_pages_render_for_administrator(
    initialized_app, initialized_database
):
    with initialized_database.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'admin'")
        account_id = cursor.fetchone()[0]
    client = initialized_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = account_id
        flask_session["username"] = "admin"
        flask_session["role"] = "admin"

    pages = {
        "/imports": "校验并导入",
        "/knowledge": "导入问答数据集",
        "/qa": "输入订单、支付、退款或售后问题",
    }
    for url, marker in pages.items():
        response = client.get(url)
        assert response.status_code == 200
        assert marker in response.get_data(as_text=True)


def test_retail_dataset_builds_complete_paid_order_chain(
    initialized_app, initialized_database
):
    from app.extensions import db
    from app.services.retail_import import RetailImportError, RetailImportService

    payload = (
        "客户编号,客户姓名,手机号,订单编号,下单时间,商品SKU,商品名称,商品分类,数量,单价,支付方式\n"
        "UP-C001,导入客户,13800000001,UP-SO001,2026-07-14 10:30:00,UP-SKU1,咖啡,饮品,2,19.90,微信\n"
        "UP-C001,导入客户,13800000001,UP-SO001,2026-07-14 10:30:00,UP-SKU2,茶,饮品,1,9.90,微信\n"
    ).encode("utf-8-sig")

    with initialized_app.test_request_context("/imports/upload"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id
        result = RetailImportService.import_dataset("orders.csv", payload, account_id)
        assert result["customer_count"] == 1
        assert result["order_count"] == 1
        assert result["skipped_orders"] == 0

        chain = db.session.execute(text("""
            SELECT order_row.status, order_row.total_amount,
                   COUNT(DISTINCT item.order_item_id) AS item_count,
                   COUNT(DISTINCT payment.payment_id) AS payment_count,
                   COUNT(DISTINCT flow.flow_id) AS flow_count
            FROM biz.sales_order order_row
            JOIN biz.order_item item ON item.order_id = order_row.order_id
            JOIN biz.payment payment ON payment.order_id = order_row.order_id
            JOIN dwd.consumption_flow flow ON flow.payment_id = payment.payment_id
            WHERE order_row.order_no = 'UP-SO001'
            GROUP BY order_row.order_id
        """)).mappings().one()
        assert chain["status"] == "paid"
        assert str(chain["total_amount"]) == "49.70"
        assert chain["item_count"] == 2
        assert chain["payment_count"] == 1
        assert chain["flow_count"] == 1
        assert db.session.execute(text("""
            SELECT stock_qty FROM biz.product WHERE sku = 'UP-SKU1'
        """)).scalar_one() == 0
        db.session.commit()

        db.session.execute(text("""
            UPDATE biz.product SET product_name = '当前商品名', unit_price = 99.00
            WHERE sku = 'UP-SKU1'
        """))
        db.session.commit()
        repeated = RetailImportService.import_dataset("orders.csv", payload, account_id)
        assert repeated["order_count"] == 0
        assert repeated["skipped_orders"] == 1
        current_product = db.session.execute(text("""
            SELECT product_name, unit_price FROM biz.product WHERE sku = 'UP-SKU1'
        """)).mappings().one()
        assert current_product["product_name"] == "当前商品名"
        assert str(current_product["unit_price"]) == "99.00"
        assert db.session.execute(text("""
            SELECT COUNT(*) FROM audit.import_log
            WHERE source_name LIKE '用户上传：orders.csv' AND status = 'success'
        """)).scalar_one() == 2
        db.session.commit()

        conflicting = payload.replace(b",2,19.90,", b",3,19.90,")
        with pytest.raises(RetailImportError, match="已存在"):
            RetailImportService.import_dataset("orders.csv", conflicting, account_id)
        failed_error = db.session.execute(text("""
            SELECT error_message FROM ods.import_batch
            WHERE status = 'failed' ORDER BY batch_id DESC LIMIT 1
        """)).scalar_one()
        assert "UP-SO001" in failed_error
        assert "INSERT INTO" not in failed_error
        db.session.commit()


def test_retail_import_preflight_stages_rows_without_writing_business_records(
    initialized_app, initialized_database
):
    from app.extensions import db
    from app.services.retail_import import RetailImportService

    payload = (
        "客户编号,客户姓名,手机号,订单编号,下单时间,商品SKU,商品名称,商品分类,数量,单价,支付方式\n"
        "PF-C001,预检客户,13800000002,PF-SO001,2026-07-20 10:30:00,PF-SKU1,咖啡,饮品,2,19.90,微信\n"
        "PF-C001,预检客户,13800000002,PF-SO001,2026-07-20 10:30:00,PF-SKU2,茶,饮品,1,9.90,微信\n"
    ).encode("utf-8-sig")

    with initialized_app.test_request_context("/imports/preview"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id

        result = RetailImportService.preflight_dataset("orders.csv", payload, account_id)

        assert result["input_row_count"] == 2
        assert result["valid_row_count"] == 2
        assert result["invalid_row_count"] == 0
        assert str(result["gross_amount"]) == "49.70"
        assert db.session.execute(text("""
            SELECT COUNT(*) FROM biz.sales_order WHERE order_no = 'PF-SO001'
        """)).scalar_one() == 0
        assert db.session.execute(text("""
            SELECT COUNT(*) FROM ods.import_stage_row row
            JOIN ods.import_batch batch ON batch.batch_id = row.batch_id
            WHERE batch.batch_no = :batch_no
        """), {"batch_no": result["batch_no"]}).scalar_one() == 2
        batch = db.session.execute(text("""
            SELECT file_sha256, input_row_count, valid_row_count, invalid_row_count,
                   confirmed_at
            FROM ods.import_batch WHERE batch_no = :batch_no
        """), {"batch_no": result["batch_no"]}).mappings().one()
        assert len(batch["file_sha256"].strip()) == 64
        assert batch["input_row_count"] == 2
        assert batch["valid_row_count"] == 2
        assert batch["invalid_row_count"] == 0
        assert batch["confirmed_at"] is None
        db.session.commit()


def test_confirmed_preflight_creates_paid_order_chain_and_reconciles_amount(
    initialized_app, initialized_database
):
    from app.extensions import db
    from app.services.retail_import import RetailImportService

    payload = (
        "客户编号,客户姓名,订单编号,下单时间,商品SKU,商品名称,数量,单价\n"
        "CF-C001,确认客户,CF-SO001,2026-07-20 10:30:00,CF-SKU1,咖啡,2,19.90\n"
        "CF-C001,确认客户,CF-SO001,2026-07-20 10:30:00,CF-SKU2,茶,1,9.90\n"
    ).encode("utf-8-sig")

    with initialized_app.test_request_context("/imports/confirm"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id

        preview = RetailImportService.preflight_dataset("confirm.csv", payload, account_id)
        result = RetailImportService.confirm_preflight(preview["batch_no"], account_id)

        assert result["order_count"] == 1
        assert str(result["gross_amount"]) == "49.70"
        batch = db.session.execute(text("""
            SELECT status, confirmed_at, gross_amount, transaction_count
            FROM ods.import_batch WHERE batch_no = :batch_no
        """), {"batch_no": preview["batch_no"]}).mappings().one()
        assert batch["status"] == "success"
        assert batch["confirmed_at"] is not None
        assert float(batch["gross_amount"]) == 49.70
        assert batch["transaction_count"] == 1
        chain_count = db.session.execute(text("""
            SELECT COUNT(*)
            FROM biz.sales_order order_row
            JOIN biz.payment payment ON payment.order_id = order_row.order_id
            JOIN dwd.consumption_flow flow ON flow.payment_id = payment.payment_id
            WHERE order_row.order_no = 'CF-SO001'
        """)).scalar_one()
        assert chain_count == 1
        db.session.commit()


def test_faq_dataset_answers_directly_and_new_version_replaces_old(
    initialized_app, initialized_database, tmp_path
):
    from app.extensions import db
    from app.services.knowledge import KnowledgeService
    from app.services.rag import RagService

    initialized_app.config["KNOWLEDGE_UPLOAD_DIR"] = str(tmp_path / "knowledge")
    question = "订单支付后多久可以申请退款？"

    with initialized_app.test_request_context("/knowledge/datasets"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id
        base_id = KnowledgeService.create_base("faq_import", "FAQ 导入", "", account_id)
        first_payload = (
            "问题,答案,分类,关键词\n"
            f"{question},支付后 7 天内可以申请退款。,退款,退款时效\n"
        ).encode("utf-8-sig")
        document_id, row_count = KnowledgeService.ingest_faq_dataset(
            base_id, "", "faq.csv", first_payload, account_id
        )
        assert row_count == 1
        KnowledgeService.publish(document_id, account_id)

        answer = RagService.ask(None, question, account_id)
        assert answer["answer"] == "支付后 7 天内可以申请退款。"
        assert answer["ticket_id"] is None
        assert answer["citations"][0]["document_id"] == document_id

        second_payload = (
            "问题,答案,分类,关键词\n"
            f"{question},支付后 15 天内可以申请退款。,退款,退款时效\n"
        ).encode("utf-8-sig")
        second_id, _ = KnowledgeService.ingest_faq_dataset(
            base_id, "", "faq-v2.csv", second_payload, account_id
        )
        KnowledgeService.publish(second_id, account_id)
        published = db.session.execute(text("""
            SELECT document_id FROM kb.document
            WHERE knowledge_base_id = :base_id AND title = '标准问答' AND is_published
        """), {"base_id": base_id}).scalars().all()
        assert published == [second_id]
        db.session.commit()

        updated = RagService.ask(answer["session_id"], question, account_id)
        assert updated["answer"] == "支付后 15 天内可以申请退款。"
        paraphrased = RagService.ask(
            updated["session_id"], "我想知道办理退费的期限有多久", account_id
        )
        assert paraphrased["answer"] == "支付后 15 天内可以申请退款。"
        basic = RagService.ask(updated["session_id"], "你好", account_id)
        assert basic["ticket_id"] is None
        assert basic["citations"] == []
        assert "智能客服" in basic["answer"]
        handoff = RagService.ask(updated["session_id"], "请转人工客服", account_id)
        assert handoff["ticket_id"] is not None
        assert "人工客服工单" in handoff["answer"]
