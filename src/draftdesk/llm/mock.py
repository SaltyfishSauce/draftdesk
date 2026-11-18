"""A rule-based stand-in for a language model.

It exists so the demo and the test suite run offline with no API key. It is not
a model: it fills templates from the order data. Real deployments configure the
`anthropic` or `openai_compatible` adapter.
"""

from __future__ import annotations

import re

from ..models import DraftRequest, DraftResult
from .base import LLMProvider

_T = {
    "en": {
        "hi": "Hi {name},",
        "thanks": "Thank you for reaching out to {store}.",
        "shipped": "Your order #{n} shipped via {carrier}. Tracking number: {track}.",
        "track_url": "You can follow it here: {url}",
        "status": "Your order #{n} is currently: {status}.",
        "no_order": "I could not find an order under this email address. Could you send us your order number so we can look into it?",
        "returns": "I have passed your request about order #{n} to our returns team, and we will confirm the next steps shortly.",
        "returns_generic": "I have passed your request to our returns team. Could you confirm your order number so we can move quickly?",
        "complaint": "I am sorry about this experience. I have escalated it to our team and we will follow up with you directly.",
        "generic": "We have received your message and a member of our team is looking into it.",
        "instruction": "(Revised as requested: {i})",
        "bye": "Best regards,",
    },
    "zh": {
        "hi": "{name}，您好：",
        "thanks": "感谢您联系{store}。",
        "shipped": "您的订单 #{n} 已通过 {carrier} 发出，运单号：{track}。",
        "track_url": "您可以在这里查看物流：{url}",
        "status": "您的订单 #{n} 当前状态为：{status}。",
        "no_order": "我们没有查到该邮箱下的订单，能否提供一下您的订单号，方便我们核实？",
        "returns": "关于订单 #{n} 的申请，我们已转交售后团队，稍后会与您确认后续步骤。",
        "returns_generic": "您的申请已转交售后团队，麻烦提供一下订单号，以便我们尽快处理。",
        "complaint": "非常抱歉给您带来不好的体验，我们已将问题升级处理，会尽快与您联系。",
        "generic": "我们已收到您的来信，团队正在跟进。",
        "instruction": "（已按要求修改：{i}）",
        "bye": "此致",
    },
    "es": {
        "hi": "Hola {name}:",
        "thanks": "Gracias por escribir a {store}.",
        "shipped": "Tu pedido #{n} fue enviado con {carrier}. Número de seguimiento: {track}.",
        "track_url": "Puedes seguirlo aquí: {url}",
        "status": "El estado actual de tu pedido #{n} es: {status}.",
        "no_order": "No encontramos ningún pedido con este correo. ¿Podrías enviarnos tu número de pedido para revisarlo?",
        "returns": "Hemos pasado tu solicitud sobre el pedido #{n} a nuestro equipo de devoluciones y te confirmaremos los siguientes pasos en breve.",
        "returns_generic": "Hemos pasado tu solicitud a nuestro equipo de devoluciones. ¿Podrías confirmar tu número de pedido?",
        "complaint": "Lamentamos mucho esta experiencia. Hemos escalado el caso y nos pondremos en contacto contigo.",
        "generic": "Hemos recibido tu mensaje y nuestro equipo lo está revisando.",
        "instruction": "(Revisado según lo solicitado: {i})",
        "bye": "Un saludo,",
    },
}

_RETURN = re.compile(r"refund|return|exchange|退款|退货|换货|reembolso|devoluci|devolver", re.I)
_COMPLAINT = re.compile(r"damaged|broken|terrible|unacceptable|worst|投诉|损坏|破损|roto|dañado", re.I)
_SHIPPING = re.compile(r"where is|track|ship|deliver|arrive|物流|发货|快递|到货|env[ií]o|llega|seguimiento", re.I)
_PRODUCT = re.compile(r"size|material|stock|available|compatible|尺寸|材质|有货|talla|disponible", re.I)
_INJECTION = re.compile(r"ignore (all |the )?(previous|prior|your) (instructions|rules)|system prompt", re.I)


def _language(text: str) -> str:
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"\b(hola|gracias|pedido|por favor|dónde|cuándo)\b|[¿¡ñ]", text, re.I):
        return "es"
    return "en"


class MockLLMProvider(LLMProvider):
    name = "mock"

    def draft(self, request: DraftRequest) -> DraftResult:
        text = f"{request.email.subject}\n{request.email.body_text}"
        t = _T[_language(text)]
        lang = _language(text)
        order = request.orders[0] if request.orders else None
        name = (request.customer.name or request.customer.email.split("@")[0]).split()[0]
        lines = [t["hi"].format(name=name), "", t["thanks"].format(store=request.store_name)]
        needs_human, notes, category = False, "", "other"

        if _INJECTION.search(text):
            category, needs_human = "other", True
            notes = "The email contains text that looks like instructions to the assistant. Reply manually."
            lines.append(t["generic"])
        elif _COMPLAINT.search(text):
            category, needs_human = "complaint", True
            notes = "Complaint: decide on compensation before sending."
            lines.append(t["complaint"])
        elif _RETURN.search(text):
            category, needs_human = "return_refund", True
            notes = "Return or refund request: check eligibility against the returns policy."
            lines.append(t["returns"].format(n=order.number) if order else t["returns_generic"])
        elif _SHIPPING.search(text) or (order and not _PRODUCT.search(text)):
            category = "shipping" if _SHIPPING.search(text) else "order_status"
            if order and order.shipments:
                s = order.shipments[0]
                lines.append(t["shipped"].format(n=order.number, carrier=s.carrier, track=s.tracking_number))
                if s.tracking_url:
                    lines.append(t["track_url"].format(url=s.tracking_url))
            elif order:
                lines.append(t["status"].format(n=order.number, status=order.status))
            else:
                needs_human = True
                notes = "No order found for this address; the draft asks for the order number."
                lines.append(t["no_order"])
        else:
            category = "product_question" if _PRODUCT.search(text) else "other"
            needs_human = True
            notes = "The demo model cannot answer open questions; write this reply or connect a real model."
            lines.append(t["generic"])

        if request.reviewer_instruction.strip():
            lines.append(t["instruction"].format(i=request.reviewer_instruction.strip()))
        lines += ["", t["bye"], request.signature]
        return DraftResult(
            body="\n".join(lines),
            language=lang,
            category=category,  # type: ignore[arg-type]
            needs_human=needs_human,
            reviewer_notes=notes,
            model="mock-rule-based",
        )
