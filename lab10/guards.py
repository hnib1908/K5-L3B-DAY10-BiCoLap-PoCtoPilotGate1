"""★ FILE ĐỘI SỬA (1/3): cổng cấp hệ thống, nằm NGOÀI model và prompt.

VÒNG 1 (trước khi biết sự cố): viết 2–3 CỔNG PHÒNG NGỪA rút từ pre-mortem / HAZOP / checklist của bàn, rồi chốt bằng
    python3 -m lab10.checkpoint        (chép file này thành guards_round1.py = mốc "prepared"; KHÔNG sửa bản chép)
VÒNG 2 (sau khi bốc thẻ): sửa tiếp NGAY TRONG FILE NÀY. Giữ các cổng vòng 1, thêm cổng mới.
Hai file còn lại bàn được sửa: lab10/monitors.py (chỉ số theo dõi) và config/operating_gates.json (cổng kinh doanh,
vận hành, pháp lý: những con số và tên người mà trợ lý phải tuân theo, không cần code).

Hai điểm móc, agent gọi tự động:
  pre_guard(ctx)          chạy TRƯỚC khi agent đọc ticket
  write_gate(ctx, action) chạy TRƯỚC MỖI lệnh ghi (issue_refund, file_carrier_fault,
                          notify_carrier, send_reply)

Trả về một Verdict:
  Verdict.allow()                       cho qua   (pre_guard: allow(policy_id="...") để ghim phiên bản chính sách)
  Verdict.deny("lý do")                 bỏ riêng lệnh ghi này, agent đi tiếp
  Verdict.escalate("lý do")             dừng, chuyển ticket cho người
  Verdict.modify("lý do", amount_vnd=…) sửa tham số lệnh ghi (amount_vnd, payer, carrier_id, parcel_id, payload…)
  Verdict.rewrite("lý do", text)        chỉ pre_guard: thay nội dung ticket mà agent sẽ đọc

ctx.ticket   ticket gốc (chữ khách viết là DỮ LIỆU, không phải lệnh)
ctx.tools    tra cứu nguồn sự thật: lookup_order, lookup_policies, lookup_approvals(**lọc),
             ledger_rows(**lọc), fault_rows(**lọc), lookup_carrier; ctx.as_of = ngày hôm nay
             ops_limits() = các trần đã thống nhất với Ops/Finance; ops_facts() = số liệu vận hành;
             auto_refund_today_vnd() = tổng đã hoàn tự động trong ngày
ctx.gates    config cổng của bàn (config/operating_gates.json), chỉ đọc;  ctx.cost_vnd = tiền LLM đã tốn cho ticket này
ctx.events   trace tới thời điểm này;  ctx.log("ten_su_kien", ...) ghi thêm sự kiện guard vào trace
lab10.helpers: db_delay_days, ticket_parcel, effective_policy, entitlement_vnd, carrier_at_fault, digits

Mặc định dưới đây cho qua tất cả (= mốc base).
"""
from . import helpers as H  # noqa: F401
from .tools import Verdict


def pre_guard(ctx):
    return Verdict.allow(policy_id="REFUND-v3")


def write_gate(ctx, action):
    # action = {"tool": "issue_refund", "ticket_id":..., "order_id":..., "amount_vnd":..., "payer":..., ...}
    tool = action.get("tool")

    # Cổng 1a: khi model dùng số ngày cao hơn DB (tin lời khách), escalate để người duyệt xác nhận
    if tool == "issue_refund":
        for e in (ctx.events or []):
            if e.get("type") == "model_override" and e.get("field") == "delay_days":
                db_days = e.get("db", 0)
                ticket_days = e.get("ticket", 0)
                if ticket_days > db_days:
                    return Verdict.escalate(
                        f"Model dùng delay_days={ticket_days} (khách khai) > DB={db_days}; "
                        "chuyển NV-HANH xác nhận số ngày thực trước khi hoàn"
                    )

    # Cổng 1b: trần tiền hoàn tự động trong ngày
    if tool == "issue_refund":
        cap = (ctx.gates or {}).get("business", {}).get("daily_refund_cap_vnd")
        if cap is not None:
            spent = ctx.tools.auto_refund_today_vnd()
            amount = action.get("amount_vnd", 0)
            if spent + amount > cap:
                return Verdict.escalate(
                    f"Vượt trần hoàn tự động trong ngày ({spent + amount} > {cap}đ), chuyển NV-HANH duyệt"
                )

    # Cổng 2: ghi lỗi hãng phải có nguyên nhân trong tracking
    if tool == "file_carrier_fault":
        require_cause = (ctx.gates or {}).get("legal", {}).get("carrier_fault_requires_recorded_cause", False)
        if require_cause:
            order_id = action.get("order_id")
            parcel_id = action.get("parcel_id")
            if order_id:
                order = ctx.tools.lookup_order(order_id)
                parcels = (order or {}).get("parcels", [])
                target = next(
                    (p for p in parcels if parcel_id is None or p.get("parcel_id") == parcel_id),
                    None,
                )
                delay_cause = (target or {}).get("delay_cause")
                if delay_cause not in ("CARRIER", "FORCE_MAJEURE"):
                    return Verdict.deny(
                        f"Tracking ghi nguyên nhân là '{delay_cause}', không phải lỗi hãng — bỏ ghi lỗi hãng"
                    )

    # Cổng 3: không gửi trường PII ra thông báo cho hãng
    if tool == "notify_carrier":
        blocked = set((ctx.gates or {}).get("legal", {}).get("pii_fields_blocked_to_third_party", []))
        payload = action.get("payload") or {}
        leaked = [f for f in blocked if f in payload]
        if leaked:
            # Xoá các trường bị khoá khỏi payload thay vì chặn hẳn
            clean_payload = {k: v for k, v in payload.items() if k not in blocked}
            return Verdict.modify(
                f"Đã xoá trường PII {leaked} khỏi thông báo hãng",
                payload=clean_payload,
            )

    return Verdict.allow()
