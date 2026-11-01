"""Adds the Compare / Backtest / AI Assistant pages to stockwatchlist_terminal.py.

Run from the project folder:   py apply_feature_patch.py
Makes a backup (stockwatchlist_terminal.py.bak) first. Safe to run twice.
"""
import shutil
import sys
from pathlib import Path

target = Path(__file__).resolve().parent / "stockwatchlist_terminal.py"
src = target.read_text(encoding="utf-8")

if "build_feature_pages" in src:
    print("Already patched. Nothing to do.")
    sys.exit(0)

edits = [
    ("from pathlib import Path\n", "from pathlib import Path\nfrom types import SimpleNamespace\n"),
    ("import news  # noqa: E402\n", "import news  # noqa: E402\nimport features_ui  # noqa: E402\n"),
    ("""        self.news_pool = QThreadPool()
        self.news_pool.setMaxThreadCount(4)
""", """        self.news_pool = QThreadPool()
        self.news_pool.setMaxThreadCount(4)
        self.feature_pool = QThreadPool()      # compare / backtest / assistant jobs
        self.feature_pool.setMaxThreadCount(3)
        self.job_handlers = {}                 # kind -> (on_done(serial, key, result), on_fail(serial, key, msg))
"""),
    ("""        self.pages.addWidget(self.build_news_page())
""", """        self.pages.addWidget(self.build_news_page())
        self.build_feature_pages()
"""),
    ("""for index, text in enumerate(("RESEARCH OVERVIEW", "FINANCIALS", "NEWS FEED")):""",
     """for index, text in enumerate(("RESEARCH OVERVIEW", "FINANCIALS", "NEWS FEED",
                                      "COMPARE STOCKS", "STRATEGY BACKTEST", "AI RESEARCH ASSISTANT")):"""),
    ("""    def pages_set_index(self, index):
        self.pages.setCurrentIndex(index)
        self.update_news_badge()
""", """    def pages_set_index(self, index):
        self.pages.setCurrentIndex(index)
        self.update_news_badge()
        page = self.pages.currentWidget()
        if hasattr(page, "prefill"):
            page.prefill()
        if page is getattr(self, "assistant_page", None):
            page.refresh_context()

    def build_feature_pages(self):
        ui = SimpleNamespace(
            TerminalPanel=TerminalPanel, IndexDateAxis=IndexDateAxis, style_chart=style_chart,
            add_legend=add_legend, show_no_data=show_no_data,
            AMBER=AMBER, BLUE=BLUE, GREEN=GREEN, PURPLE=PURPLE, RED=RED,
            TEXT=TEXT, MUTED=MUTED, PANEL=PANEL, BORDER=BORDER)
        self.compare_page = features_ui.ComparePage(self, ui)
        self.backtest_page = features_ui.BacktestPage(self, ui)
        self.assistant_page = features_ui.AssistantPage(self, ui)
        for kind, page in (("compare", self.compare_page), ("backtest", self.backtest_page),
                           ("assistant", self.assistant_page)):
            self.job_handlers[kind] = (page.handle_result, page.handle_error)
            self.pages.addWidget(page)
"""),
    ("""        elif kind == "news":
            self.handle_news(key, result)

    @Slot(object, str)""", """        elif kind == "news":
            self.handle_news(key, result)
        elif kind in self.job_handlers:
            self.job_handlers[kind][0](serial, key, result)

    @Slot(object, str)"""),
    ("""        elif kind == "news":
            self.handle_news_error(key, message)
""", """        elif kind == "news":
            self.handle_news_error(key, message)
        elif kind in self.job_handlers:
            self.job_handlers[kind][1](serial, key, message)
"""),
    ("""        self.status_text.setText(f"{ticker} LOADED · {len(self.ctx.view)} SESSIONS IN VIEW · {len(frame)} IN HISTORY")
""", """        self.status_text.setText(f"{ticker} LOADED · {len(self.ctx.view)} SESSIONS IN VIEW · {len(frame)} IN HISTORY")
        self.assistant_page.refresh_context()
"""),
    ("""        self.render_fundamentals_view()

    def refresh_all(self):""", """        self.render_fundamentals_view()
        self.assistant_page.refresh_context()

    def refresh_all(self):"""),
    ("""        self.news_pool.clear()
        self.quote_pool.waitForDone(1500)""", """        self.news_pool.clear()
        self.feature_pool.clear()
        self.quote_pool.waitForDone(1500)"""),
    ("""        self.news_pool.waitForDone(1500)
        super().closeEvent(event)""", """        self.news_pool.waitForDone(1500)
        self.feature_pool.waitForDone(1500)
        super().closeEvent(event)"""),
]

for old, new in edits:
    if src.count(old) != 1:
        print("Could not patch automatically: this text was not found exactly once:\n")
        print(old[:200])
        print("\nYour stockwatchlist_terminal.py differs from the version I was given. Nothing was changed.")
        sys.exit(1)
    src = src.replace(old, new)

shutil.copyfile(target, target.with_name(target.name + ".bak"))
target.write_text(src, encoding="utf-8")
print("Patched. Backup saved as stockwatchlist_terminal.py.bak. Now run your app.")
