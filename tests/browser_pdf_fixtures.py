"""Synthetic original files for browser tests; no enterprise content."""
import fitz

QUOTE = "Manager approval is required."
CHINESE_QUOTE = "差旅报销需要部门经理审批。"


def make_pdf(kind="text"):
    with fitz.open() as document:
        for index, color in enumerate([(0.1, 0.3, 0.85), (0.85, 0.2, 0.1)]):
            page = document.new_page(width=480, height=620)
            page.insert_text((35, 45), f"INTERNAL TRAVEL POLICY - PAGE {index + 1}", fontsize=16)
            page.insert_text((35, 90), CHINESE_QUOTE if kind == "chinese" else QUOTE,
                fontname="china-s" if kind == "chinese" else "helv", fontsize=13)
            page.draw_rect(fitz.Rect(35, 130, 240, 270), color=color, fill=color)
            page.insert_text((35, 305), f"Synthetic document. Page {index + 1} of 2.", fontsize=11)
            if kind == "duplicate":
                page.insert_text((35, 340), QUOTE, fontsize=13)
        if kind == "encrypted":
            return document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="test-password")
        if kind == "scanned":
            image = document[0].get_pixmap().tobytes("png")
            with fitz.open() as scanned:
                page = scanned.new_page(width=480, height=620)
                page.insert_image(page.rect, stream=image)
                return scanned.tobytes()
        return document.tobytes()
