from __future__ import annotations

import argparse
import hmac
import json
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from backend.health_vault.vault_question_recovery import (
    credential_to_passphrase,
    derive_recovery_credential,
    normalize_answer,
)


class PrivateEnrollmentError(RuntimeError):
    pass


def load_selection(path: Path) -> dict:
    data = json.loads(
        path.read_text(encoding="utf-8-sig")
    )

    if data.get("version") != 1:
        raise PrivateEnrollmentError(
            "unsupported_selection_version"
        )

    questions = data.get("selected_questions")

    if not isinstance(questions, list) or len(questions) != 10:
        raise PrivateEnrollmentError(
            "expected_exactly_10_selected_questions"
        )

    salt = bytes.fromhex(data["question_salt_hex"])

    if len(salt) != 16:
        raise PrivateEnrollmentError(
            "invalid_question_salt"
        )

    if data.get("answers_collected") is not False:
        raise PrivateEnrollmentError(
            "answers_already_collected"
        )

    return data


def derive_confirmed_passphrase(
    answers: list[str],
    confirmations: list[str],
    salt: bytes,
) -> str:

    if len(answers) != 10 or len(confirmations) != 10:
        raise PrivateEnrollmentError(
            "expected_10_answer_pairs"
        )

    normalized_answers = [
        normalize_answer(value)
        for value in answers
    ]

    normalized_confirmations = [
        normalize_answer(value)
        for value in confirmations
    ]

    for left, right in zip(
        normalized_answers,
        normalized_confirmations,
    ):
        if not hmac.compare_digest(
            left.encode("utf-8"),
            right.encode("utf-8"),
        ):
            raise PrivateEnrollmentError(
                "answer_confirmation_mismatch"
            )

    first = derive_recovery_credential(
        normalized_answers,
        salt,
    )

    second = derive_recovery_credential(
        normalized_confirmations,
        salt,
    )

    if not hmac.compare_digest(first, second):
        raise PrivateEnrollmentError(
            "derived_credential_mismatch"
        )

    return credential_to_passphrase(first)


def nonsecret_completion_payload(selection: dict) -> dict:
    return {
        "version": 1,
        "profile_id": selection["profile_id"],
        "recovery_id": selection["recovery_id"],
        "selected_question_count": 10,
        "answers_collected": True,
        "plaintext_answers_stored": False,
        "answer_hashes_stored": False,
        "recovery_passphrase_stored": False,
    }


class EnrollmentWindow:

    def __init__(
        self,
        root: tk.Tk,
        selection: dict,
        completion_path: Path,
    ) -> None:

        self.root = root
        self.selection = selection
        self.completion_path = completion_path

        self.answer_entries: list[ttk.Entry] = []
        self.confirm_entries: list[ttk.Entry] = []
        self.show_vars: list[tk.BooleanVar] = []

        self.completed = False
        self.recovery_passphrase: str | None = None

        root.title(
            "HealthChecker - Private Recovery Answer Enrollment"
        )

        root.geometry("1100x820")
        root.minsize(950, 700)

        root.attributes("-topmost", True)
        root.after(
            1200,
            lambda: root.attributes("-topmost", False),
        )
        root.lift()
        root.focus_force()

        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Complete your recovery profile",
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            outer,
            text=(
                "Answer each selected question twice. "
                "Answers are used only in memory to derive your "
                "recovery credential and are not written to the "
                "completion record."
            ),
            wraplength=1020,
        ).pack(anchor="w", pady=(8, 12))

        canvas = tk.Canvas(
            outer,
            highlightthickness=0,
        )

        scrollbar = ttk.Scrollbar(
            outer,
            orient="vertical",
            command=canvas.yview,
        )

        body = ttk.Frame(canvas)

        body.bind(
            "<Configure>",
            lambda event: canvas.configure(
                scrollregion=canvas.bbox("all")
            ),
        )

        canvas.create_window(
            (0, 0),
            window=body,
            anchor="nw",
        )

        canvas.configure(
            yscrollcommand=scrollbar.set
        )

        canvas.pack(
            side="left",
            fill="both",
            expand=True,
        )

        scrollbar.pack(
            side="right",
            fill="y",
        )

        for number, item in enumerate(
            selection["selected_questions"],
            1,
        ):

            frame = ttk.LabelFrame(
                body,
                text=f"{number}. {item['prompt']}",
                padding=10,
            )

            frame.pack(
                fill="x",
                padx=4,
                pady=7,
            )

            ttk.Label(
                frame,
                text="Answer",
            ).grid(
                row=0,
                column=0,
                sticky="w",
                padx=(0, 8),
                pady=4,
            )

            answer = ttk.Entry(
                frame,
                width=72,
                show="*",
            )

            answer.grid(
                row=0,
                column=1,
                sticky="ew",
                pady=4,
            )

            ttk.Label(
                frame,
                text="Confirm answer",
            ).grid(
                row=1,
                column=0,
                sticky="w",
                padx=(0, 8),
                pady=4,
            )

            confirm = ttk.Entry(
                frame,
                width=72,
                show="*",
            )

            confirm.grid(
                row=1,
                column=1,
                sticky="ew",
                pady=4,
            )

            show_var = tk.BooleanVar(
                value=False
            )

            def toggle(
                a=answer,
                c=confirm,
                v=show_var,
            ):
                display = "" if v.get() else "*"
                a.configure(show=display)
                c.configure(show=display)

            ttk.Checkbutton(
                frame,
                text="Show",
                variable=show_var,
                command=toggle,
            ).grid(
                row=0,
                column=2,
                rowspan=2,
                padx=(10, 0),
            )

            frame.columnconfigure(
                1,
                weight=1,
            )

            self.answer_entries.append(answer)
            self.confirm_entries.append(confirm)
            self.show_vars.append(show_var)

        controls = ttk.Frame(body)
        controls.pack(
            fill="x",
            padx=4,
            pady=16,
        )

        ttk.Button(
            controls,
            text="Cancel",
            command=self.cancel,
        ).pack(side="right")

        ttk.Button(
            controls,
            text="Complete recovery profile",
            command=self.finish,
        ).pack(
            side="right",
            padx=(0, 12),
        )

        root.protocol(
            "WM_DELETE_WINDOW",
            self.cancel,
        )

    def finish(self) -> None:

        answers = [
            entry.get()
            for entry in self.answer_entries
        ]

        confirmations = [
            entry.get()
            for entry in self.confirm_entries
        ]

        if any(
            not value.strip()
            for value in answers + confirmations
        ):
            messagebox.showerror(
                "HealthChecker Recovery",
                "Every answer and confirmation is required.",
            )
            return

        try:
            salt = bytes.fromhex(
                self.selection["question_salt_hex"]
            )

            passphrase = derive_confirmed_passphrase(
                answers,
                confirmations,
                salt,
            )

        except Exception:
            messagebox.showerror(
                "HealthChecker Recovery",
                "One or more answer confirmations do not match. "
                "Please review the entries.",
            )
            return

        self.recovery_passphrase = passphrase

        payload = nonsecret_completion_payload(
            self.selection
        )

        self.completion_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.completion_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.completed = True

        messagebox.showinfo(
            "HealthChecker Recovery",
            "Your recovery answers have been verified. "
            "HealthChecker did not save the plaintext answers.",
        )

        self.root.destroy()

    def cancel(self) -> None:
        self.root.destroy()


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--selection",
        required=True,
    )

    parser.add_argument(
        "--completion",
        required=True,
    )

    args = parser.parse_args()

    selection = load_selection(
        Path(args.selection)
    )

    root = tk.Tk()

    window = EnrollmentWindow(
        root,
        selection,
        Path(args.completion),
    )

    root.mainloop()

    # Recovery credential intentionally dies with this process.
    window.recovery_passphrase = None

    return 0 if window.completed else 2


if __name__ == "__main__":
    raise SystemExit(main())