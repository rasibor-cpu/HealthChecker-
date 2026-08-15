from __future__ import annotations

import argparse
import json
import secrets
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from backend.health_vault.recovery_question_bank import (
    QUESTION_BANK,
    REQUIRED_SELECTION_COUNT,
    categories,
)


def generate_profile_id() -> str:
    return "HCUSER-" + uuid.uuid4().hex


def generate_recovery_id() -> str:
    return "HCREC-" + uuid.uuid4().hex


class RecoveryQuestionSelector:

    def __init__(self, root: tk.Tk, output: Path) -> None:
        self.root = root
        self.output = output
        self.required = REQUIRED_SELECTION_COUNT
        self.variables: dict[str, tk.BooleanVar] = {}
        self.completed = False

        root.title("HealthChecker - Password Recovery Questions")
        root.attributes("-topmost", True)
        root.after(1200, lambda: root.attributes("-topmost", False))
        root.lift()
        root.focus_force()
        root.geometry("1050x760")
        root.minsize(900, 650)

        container = ttk.Frame(root, padding=18)
        container.pack(fill="both", expand=True)

        ttk.Label(
            container,
            text="Choose your password-recovery questions",
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            container,
            text=(
                "Select exactly 10 questions whose answers you expect "
                "to remember for many years. HealthChecker assigns all "
                "technical recovery settings automatically."
            ),
            wraplength=980,
        ).pack(anchor="w", pady=(8, 12))

        self.counter_text = tk.StringVar(
            value=f"Selected: 0 / {self.required}"
        )

        ttk.Label(
            container,
            textvariable=self.counter_text,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)

        for category in categories():

            page = ttk.Frame(notebook, padding=12)
            notebook.add(page, text=category)

            questions = [
                q for q in QUESTION_BANK
                if q.category == category
            ]

            for question in questions:

                variable = tk.BooleanVar(value=False)
                self.variables[question.question_id] = variable

                ttk.Checkbutton(
                    page,
                    text=question.prompt,
                    variable=variable,
                    command=lambda qid=question.question_id:
                        self.selection_changed(qid),
                ).pack(
                    anchor="w",
                    fill="x",
                    padx=4,
                    pady=9,
                )

        controls = ttk.Frame(container)
        controls.pack(fill="x", pady=(14, 0))

        ttk.Button(
            controls,
            text="Cancel",
            command=self.cancel,
        ).pack(side="right")

        self.continue_button = ttk.Button(
            controls,
            text="Continue",
            command=self.finish,
            state="disabled",
        )

        self.continue_button.pack(
            side="right",
            padx=(0, 12),
        )

        root.protocol(
            "WM_DELETE_WINDOW",
            self.cancel,
        )

    def selected_ids(self) -> list[str]:
        return [
            qid
            for qid, variable in self.variables.items()
            if variable.get()
        ]

    def selection_changed(self, changed_id: str) -> None:

        selected = self.selected_ids()

        if len(selected) > self.required:

            self.variables[changed_id].set(False)

            messagebox.showinfo(
                "HealthChecker Recovery",
                "You may select exactly 10 questions. "
                "Unselect one before choosing another.",
            )

            selected = self.selected_ids()

        count = len(selected)

        self.counter_text.set(
            f"Selected: {count} / {self.required}"
        )

        self.continue_button.configure(
            state=(
                "normal"
                if count == self.required
                else "disabled"
            )
        )

    def finish(self) -> None:

        selected_ids = self.selected_ids()

        if len(selected_ids) != self.required:
            return

        selected_questions = [
            {
                "id": q.question_id,
                "category": q.category,
                "prompt": q.prompt,
            }
            for q in QUESTION_BANK
            if q.question_id in selected_ids
        ]

        payload = {
            "version": 1,
            "profile_id": generate_profile_id(),
            "recovery_id": generate_recovery_id(),
            "question_salt_hex": secrets.token_bytes(16).hex(),
            "selected_question_count": self.required,
            "selected_questions": selected_questions,
            "answers_collected": False,
        }

        self.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.completed = True

        self.output.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.root.destroy()

    def cancel(self) -> None:
        self.root.destroy()


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--output",
        required=True,
    )

    args = parser.parse_args()

    root = tk.Tk()

    selector = RecoveryQuestionSelector(
        root,
        Path(args.output),
    )

    root.mainloop()

    return 0 if selector.completed else 2


if __name__ == "__main__":
    raise SystemExit(main())