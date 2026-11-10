import re
import time
import uuid
from datetime import datetime

import streamlit as st

st.set_page_config(page_title="쇼핑리스트", page_icon="🛒", layout="centered")

NAME_MAX_CHARS = 50
QTY_MIN, QTY_MAX = 1, 999
DUPLICATE_SUBMIT_SECONDS = 1.5  # 추가 버튼 연타/Enter 연타로 같은 아이템이 중복 추가되는 것을 막는 시간

# 모바일(좁은 화면)에서도 입력 폼과 아이템 한 줄이 세로로 쪼개지지 않게 하고,
# 아이템 줄의 ✏️/🗑️ 버튼 터치 영역을 넉넉하게(높이 44px) 만드는 최소 CSS
CUSTOM_CSS = """
<style>
.st-key-item_list [data-testid="stHorizontalBlock"],
.st-key-add_area [data-testid="stHorizontalBlock"],
.st-key-undo_bar [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap;
    gap: 0.5rem;
}
.st-key-item_list [data-testid="stColumn"],
.st-key-add_area [data-testid="stColumn"],
.st-key-undo_bar [data-testid="stColumn"] {
    min-width: 0;
}
.st-key-add_area button p,
.st-key-undo_bar button p {
    white-space: nowrap;
}
.st-key-item_list button[kind="tertiary"] {
    min-height: 44px;
}
</style>
"""


# ---------- 상태 / 저장소 ----------

def init_state():
    """세션 상태 기본값을 초기화합니다."""
    defaults = {
        "items": [],
        "editing_id": None,      # 수정 중인 아이템 id
        "confirm_clear": False,  # 전체 삭제 확인 단계 표시 여부
        "add_warning": None,     # 추가 폼 경고 문구
        "edit_warning": None,    # 수정 저장 경고 문구
        "toast": None,           # 다음 화면에 띄울 알림 메시지
        "celebrate": False,      # 모두 체크 완료 시 풍선 효과
        "last_deleted": None,    # 되돌리기용: [(원래 위치, 아이템), ...]
        "undo_message": "",      # 되돌리기 바에 표시할 문구
        "last_add": None,        # 중복 제출 방지용: (이름, 수량, 시각)
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def load_items():
    """저장된 아이템 리스트를 불러옵니다. (현재는 session_state 사용)"""
    return st.session_state["items"]


def save_items(items):
    """아이템 리스트를 저장합니다. (현재는 session_state 사용)"""
    st.session_state["items"] = items


def find_item(item_id):
    """id로 아이템을 찾습니다. 없으면 None을 반환합니다."""
    return next((item for item in load_items() if item["id"] == item_id), None)


# ---------- 문구 / 검증 도우미 ----------

def escape_md(text):
    """사용자 입력이 마크다운 문법으로 해석되지 않도록 특수문자를 이스케이프합니다."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!~|<>:$])", r"\\\1", text)


def display_text(text):
    """사용자 입력을 st.markdown에 안전하게 넣을 수 있는 문자열로 바꿉니다.

    마크다운 문법을 이스케이프하고, URL·이메일이 자동으로 링크가 되지 않도록
    보이지 않는 문자(U+2060)를 끼워 넣습니다.
    """
    wj = "⁠"
    text = text.replace("://", f":{wj}//").replace("@", f"@{wj}")
    text = re.sub(r"(?i)\bw(ww\.)", rf"w{wj}\1", text)
    return escape_md(text)


def shorten(text, limit=15):
    """알림에 넣기 좋게 긴 이름을 줄입니다."""
    return text if len(text) <= limit else text[:limit] + "…"


def eul_reul(word):
    """단어 뒤에 붙을 목적격 조사(을/를)를 고릅니다."""
    last = word[-1]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - ord("가")) % 28 else "를"
    return "을(를)"


def with_eul_reul(name):
    """'우유를', '계란을'처럼 (줄인) 이름과 조사를 붙인 표시용 문자열을 만듭니다."""
    short = shorten(name)
    return f"{display_text(short)}{eul_reul(short)}"


def validate(name, quantity):
    """이름·수량을 검증해 문제가 있으면 경고 문구를, 없으면 None을 반환합니다."""
    if not name:
        return "아이템 이름을 입력해 주세요."
    if quantity is None or not QTY_MIN <= quantity <= QTY_MAX:
        return f"수량은 {QTY_MIN}~{QTY_MAX} 사이로 입력해 주세요."
    return None


def notify(message):
    """다음 화면 갱신 때 띄울 토스트 알림을 예약합니다."""
    st.session_state["toast"] = message


def clear_undo():
    """되돌리기 바를 닫습니다."""
    st.session_state["last_deleted"] = None


# ---------- 동작 (위젯 콜백) ----------

def add_item(name, quantity=1):
    """새 아이템을 만들어 리스트에 추가합니다."""
    item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": int(quantity),
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_items(load_items() + [item])


def on_add():
    """추가 폼 제출 시 호출됩니다. 검증에 실패하면 입력값을 그대로 두고 경고를 띄웁니다."""
    name = st.session_state["new_name"].strip()
    quantity = st.session_state["new_quantity"]
    warning = validate(name, quantity)
    st.session_state["add_warning"] = warning
    if warning:
        return

    # 입력칸은 서버에서 비우기 때문에(검증 실패 시 입력을 보존하려고 clear_on_submit 미사용)
    # 그 사이 연타된 제출은 같은 값으로 다시 들어온다. 직전과 같은 값이 바로 들어오면 무시한다.
    now = time.monotonic()
    last = st.session_state["last_add"]
    if last and last[:2] == (name, quantity) and now - last[2] < DUPLICATE_SUBMIT_SECONDS:
        return
    st.session_state["last_add"] = (name, quantity, now)

    add_item(name, quantity)
    clear_undo()
    st.session_state["new_name"] = ""
    st.session_state["new_quantity"] = 1
    notify(f"🛒 {with_eul_reul(name)} 추가했어요")


def toggle_item(item_id):
    """체크박스 값에 맞춰 아이템의 구매 완료 상태를 바꿉니다."""
    item = find_item(item_id)
    if not item:
        return
    item["checked"] = st.session_state[f"check_{item_id}"]
    items = load_items()
    save_items(items)
    clear_undo()
    if item["checked"] and all(i["checked"] for i in items):
        st.session_state["celebrate"] = True


def start_edit(item_id):
    """해당 아이템을 수정 모드로 전환합니다."""
    st.session_state.pop(f"edit_name_{item_id}", None)
    st.session_state.pop(f"edit_qty_{item_id}", None)
    st.session_state["editing_id"] = item_id
    st.session_state["edit_warning"] = None


def save_edit(item_id):
    """수정 내용을 저장합니다. 검증에 실패하면 수정 모드를 유지하고 경고를 띄웁니다."""
    name = st.session_state[f"edit_name_{item_id}"].strip()
    quantity = st.session_state[f"edit_qty_{item_id}"]
    warning = validate(name, quantity)
    st.session_state["edit_warning"] = warning
    if warning:
        return
    item = find_item(item_id)
    if item:
        item["name"] = name
        item["quantity"] = int(quantity)
        save_items(load_items())
        clear_undo()
        notify("✏️ 수정했어요")
    cancel_edit()


def cancel_edit():
    """수정 모드를 종료합니다. (변경 사항은 버려집니다)"""
    st.session_state["editing_id"] = None
    st.session_state["edit_warning"] = None


def delete_items(ids, message):
    """주어진 id의 아이템들을 삭제하고, 되돌릴 수 있도록 기록해 둡니다."""
    ids = set(ids)
    items = load_items()
    removed = [(index, item) for index, item in enumerate(items) if item["id"] in ids]
    if not removed:
        return
    save_items([item for item in items if item["id"] not in ids])
    st.session_state["last_deleted"] = removed
    st.session_state["undo_message"] = message
    if st.session_state["editing_id"] in ids:
        cancel_edit()
    if not load_items():
        set_confirm_clear(False)


def undo_delete():
    """마지막 삭제를 되돌려 아이템을 원래 위치에 복원합니다."""
    removed = st.session_state["last_deleted"]
    if not removed:
        return
    items = list(load_items())
    for index, item in removed:  # 원래 위치 오름차순이므로 앞에서부터 끼워 넣으면 됨
        items.insert(index, item)
    save_items(items)
    clear_undo()
    notify("↩️ 되돌렸어요")


def delete_one(item_id):
    """아이템 하나를 삭제합니다."""
    item = find_item(item_id)
    if item:
        delete_items([item_id], f"🗑️ {with_eul_reul(item['name'])} 삭제했어요")


def delete_checked():
    """체크된 아이템을 모두 삭제합니다."""
    ids = [item["id"] for item in load_items() if item["checked"]]
    delete_items(ids, f"🗑️ 구매 완료한 {len(ids)}개 항목을 삭제했어요")


def set_confirm_clear(value):
    """전체 삭제 확인 단계를 열거나 닫습니다."""
    st.session_state["confirm_clear"] = value


def clear_all():
    """모든 아이템을 삭제합니다."""
    ids = [item["id"] for item in load_items()]
    delete_items(ids, f"🗑️ 아이템 {len(ids)}개를 모두 삭제했어요")
    set_confirm_clear(False)


# ---------- 화면 ----------

def render_header():
    """제목과 구매 진행 상황을 표시합니다."""
    st.title("🛒 쇼핑리스트")
    items = load_items()
    if not items:
        st.caption("살 물건을 추가하고, 장 보면서 하나씩 체크해 보세요.")
        return

    done = sum(item["checked"] for item in items)
    total = len(items)
    with st.container(border=True):
        st.progress(
            done / total,
            text=f"**{done} / {total}** 구매 완료 · {round(done / total * 100)}%",
        )
        if done == total:
            st.success("모두 샀어요! 장보기 완료 🎉")


def render_add_form():
    """아이템 이름과 수량을 입력받아 추가하는 폼을 그립니다."""
    with st.container(key="add_area"):
        with st.form("add_form"):
            col_name, col_qty, col_btn = st.columns([4.6, 2.2, 2.2], vertical_alignment="bottom")
            col_name.text_input(
                "아이템 이름", key="new_name", placeholder="예: 우유, 계란, 사과",
                max_chars=NAME_MAX_CHARS,
            )
            col_qty.number_input("수량", value=1, step=1, key="new_quantity")
            col_btn.form_submit_button("추가", type="primary", on_click=on_add, width="stretch")

    if st.session_state["add_warning"]:
        st.warning(st.session_state["add_warning"])


def render_item_row(item):
    """아이템 한 줄(체크박스, 이름, 수량, 수정/삭제 버튼)을 그립니다."""
    item_id = item["id"]
    editing_other = st.session_state["editing_id"] is not None
    col_check, col_name, col_qty, col_edit, col_del = st.columns(
        [0.8, 5, 1.3, 1.5, 1.5], vertical_alignment="center"
    )
    col_check.checkbox(
        "구매 완료",
        value=item["checked"],
        key=f"check_{item_id}",
        on_change=toggle_item,
        args=(item_id,),
        label_visibility="collapsed",
    )

    name = display_text(item["name"])
    if item["checked"]:
        col_name.markdown(f":gray[~~{name}~~]")
        col_qty.markdown(f":gray[x{item['quantity']}]")
    else:
        col_name.markdown(name)
        col_qty.markdown(f"x{item['quantity']}")

    col_edit.button(
        "✏️", key=f"edit_{item_id}", type="tertiary",
        help="먼저 수정 중인 항목을 저장하거나 취소해 주세요" if editing_other else "수정하기",
        disabled=editing_other,
        on_click=start_edit, args=(item_id,), width="stretch",
    )
    col_del.button(
        "🗑️", key=f"del_{item_id}", help="삭제하기", type="tertiary",
        on_click=delete_one, args=(item_id,), width="stretch",
    )


def render_edit_row(item):
    """수정 모드인 아이템(이름/수량 입력, 저장/취소 버튼)을 그립니다."""
    item_id = item["id"]
    with st.container(border=True):
        col_name, col_qty = st.columns([1, 1], vertical_alignment="bottom")
        col_name.text_input(
            "이름", value=item["name"], key=f"edit_name_{item_id}", max_chars=NAME_MAX_CHARS
        )
        col_qty.number_input(
            "수량", value=item["quantity"], step=1, key=f"edit_qty_{item_id}"
        )
        if st.session_state["edit_warning"]:
            st.warning(st.session_state["edit_warning"])
        col_save, col_cancel = st.columns(2)
        col_save.button(
            "저장", key=f"save_{item_id}", icon=":material/check:", type="primary",
            on_click=save_edit, args=(item_id,), width="stretch",
        )
        col_cancel.button(
            "취소", key=f"cancel_{item_id}", icon=":material/close:",
            on_click=cancel_edit, width="stretch",
        )


def render_undo_bar():
    """방금 삭제한 내용을 알려 주고 되돌리기 버튼을 보여줍니다."""
    if not st.session_state["last_deleted"]:
        return
    with st.container(border=True, key="undo_bar"):
        col_msg, col_btn = st.columns([6, 4], vertical_alignment="center")
        col_msg.markdown(st.session_state["undo_message"])
        col_btn.button("되돌리기", icon=":material/undo:", on_click=undo_delete, width="stretch")


def render_list():
    """아이템 목록을 표시합니다. 아직 안 산 아이템이 위, 산 아이템이 아래에 옵니다."""
    render_undo_bar()
    items = load_items()
    if not items:
        st.info("아직 추가된 아이템이 없어요. 위에서 첫 아이템을 추가해 보세요!", icon="📝")
        return

    with st.container(border=True, key="item_list"):
        for item in sorted(items, key=lambda i: i["checked"]):
            if item["id"] == st.session_state["editing_id"]:
                render_edit_row(item)
            else:
                render_item_row(item)


def render_bulk_actions():
    """체크된 항목 삭제 / 전체 삭제 버튼을 그립니다."""
    items = load_items()
    if not items:
        return

    has_checked = any(item["checked"] for item in items)
    col_checked, col_all = st.columns(2)
    col_checked.button(
        "체크된 항목 삭제",
        icon=":material/playlist_remove:",
        on_click=delete_checked,
        disabled=not has_checked,
        width="stretch",
    )
    col_all.button(
        "전체 삭제",
        icon=":material/delete_sweep:",
        on_click=set_confirm_clear,
        args=(True,),
        width="stretch",
    )

    if st.session_state["confirm_clear"]:
        with st.container(border=True):
            st.warning(f"아이템 {len(items)}개를 정말 모두 삭제할까요?")
            col_yes, col_no = st.columns(2)
            col_yes.button("삭제할게요", on_click=clear_all, type="primary", width="stretch")
            col_no.button(
                "취소", key="cancel_clear", on_click=set_confirm_clear, args=(False,),
                width="stretch",
            )


def show_feedback():
    """예약된 토스트 알림과 풍선 효과를 한 번만 보여줍니다."""
    if st.session_state["toast"]:
        st.toast(st.session_state["toast"])
        st.session_state["toast"] = None
    if st.session_state["celebrate"]:
        st.balloons()
        st.session_state["celebrate"] = False


def main():
    """앱 진입점입니다."""
    init_state()
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    render_header()
    render_add_form()
    render_list()
    render_bulk_actions()
    show_feedback()


main()
