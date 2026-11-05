import { useEffect, useRef, useState, type FormEvent, type PointerEvent } from "react";
import "./BugReportDialog.css";

// Prepares a GitHub issue locally (modelled on ml-peg.stfc.ac.uk's reporter):
// nothing is submitted from the app. Issue links can't carry files, so a
// captured screenshot is copied to the clipboard for the user to paste into
// the issue, or downloaded where the clipboard isn't available.

const ISSUE_URL = "https://github.com/stfc/goldilocks-agent/issues/new";
// Form fields are prefilled by their ids in .github/ISSUE_TEMPLATE/agent-app-bug.yml.
const ISSUE_TEMPLATE = "agent-app-bug.yml";
// Bound the encoded link, including the title, report text and attachment note.
const MAX_ISSUE_URL_LENGTH = 7500;
const MAX_SHOT_WIDTH = 1920;
const MAX_SHOT_HEIGHT = 1080;
// Stands in for images html-to-image can't fetch (cross-origin, broken src).
const IMAGE_PLACEHOLDER =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=";

// What the reporter sees under "Included page details" and what lands in the
// issue's Page details field. `page` also seeds the default title.
export interface BugReportContext {
  page: string;
  [key: string]: unknown;
}

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface Screenshot {
  image: HTMLImageElement;
  width: number;
  height: number;
}

interface ReportFields {
  title: string;
  description: string;
  expected: string;
  steps: string;
}

interface BugReportDialogProps {
  context: BugReportContext;
  onClose: () => void;
  t: (key: string) => string;
}

function screenshotFilename(): string {
  return `goldilocks-bug-screenshot-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
}

function attachmentInstructions(step: string): string {
  return "> [!IMPORTANT]\n> **Complete this manual step before submitting.**\n"
    + "> Attachments are not uploaded automatically.\n\n"
    + "- [ ] **Attach the screenshot:** " + step;
}

const PASTE_INSTRUCTIONS = attachmentInstructions(
  "it was copied to your clipboard. Click in this field and paste it (⌘V / Ctrl+V).",
);

function dropInstructions(filename: string): string {
  return attachmentInstructions("drag `" + filename + "` from your browser's downloads into this field.");
}

// Returns the prefilled new-issue URL, or null if it's too long for GitHub.
function buildIssueUrl(fields: ReportFields, context: BugReportContext, screenshotNote: string | null): string | null {
  const url = new URL(ISSUE_URL);
  url.searchParams.set("template", ISSUE_TEMPLATE);
  url.searchParams.set("title", fields.title.trim() || context.page);
  for (const id of ["description", "expected", "steps"] as const) {
    const value = fields[id].trim();
    if (value) url.searchParams.set(id, value);
  }
  if (screenshotNote) url.searchParams.set("screenshots", screenshotNote);
  url.searchParams.set("details", JSON.stringify(context, null, 2));
  if (url.href.length <= MAX_ISSUE_URL_LENGTH) return url.href;
  url.searchParams.set("details", JSON.stringify(context));
  return url.href.length <= MAX_ISSUE_URL_LENGTH ? url.href : null;
}

function canvasPng(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("PNG encoding failed"))), "image/png");
  });
}

// Resolves false where the async clipboard API is missing (it needs HTTPS or
// localhost) or the browser refuses the write.
async function copyCanvas(canvas: HTMLCanvasElement): Promise<boolean> {
  if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") return false;
  try {
    // write() is reached before the first await, i.e. still inside the click:
    // Safari rejects clipboard writes made after it. The PNG is handed over as
    // a promise for the same reason.
    await navigator.clipboard.write([new ClipboardItem({ "image/png": canvasPng(canvas) })]);
    return true;
  } catch {
    return false;
  }
}

async function downloadCanvas(canvas: HTMLCanvasElement, filename: string) {
  const url = URL.createObjectURL(await canvasPng(canvas));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function openInNewTab(url: string) {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.click();
}

export default function BugReportDialog({ context, onClose, t }: BugReportDialogProps) {
  const scrimRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  // Bumped when a screenshot is removed, so a capture still in flight is dropped.
  const captureGenerationRef = useRef(0);
  const [fields, setFields] = useState<ReportFields>(() => ({
    title: `${context.page.slice(0, 100)}: `,
    description: "",
    expected: "",
    steps: "",
  }));
  const [screenshot, setScreenshot] = useState<Screenshot | null>(null);
  const [marks, setMarks] = useState<Rect[]>([]);
  const [pendingMark, setPendingMark] = useState<Rect | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    const input = titleRef.current;
    if (!input) return;
    input.focus({ preventScroll: true });
    input.setSelectionRange(input.value.length, input.value.length);
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !screenshot) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(screenshot.image, 0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#e11d48";
    ctx.fillStyle = "rgba(225, 29, 72, 0.10)";
    ctx.lineWidth = Math.max(3, canvas.width / 450);
    for (const mark of pendingMark ? [...marks, pendingMark] : marks) {
      ctx.fillRect(mark.x, mark.y, mark.width, mark.height);
      ctx.strokeRect(mark.x, mark.y, mark.width, mark.height);
    }
  }, [screenshot, marks, pendingMark]);

  function updateField(id: keyof ReportFields, value: string) {
    setFields((prev) => ({ ...prev, [id]: value }));
  }

  async function capture() {
    const generation = ++captureGenerationRef.current;
    setCapturing(true);
    setStatus(t("report_bug_capturing"));
    document.body.classList.add("bug-report-capturing");
    try {
      // Loaded on first use: only reporters need it.
      const { toPng } = await import("html-to-image");
      const width = window.innerWidth;
      const height = window.innerHeight;
      const data = await toPng(document.body, {
        width,
        height,
        pixelRatio: Math.min(1, MAX_SHOT_WIDTH / width, MAX_SHOT_HEIGHT / height),
        backgroundColor: getComputedStyle(document.body).backgroundColor,
        imagePlaceholder: IMAGE_PLACEHOLDER,
        filter: (node) => {
          if (node === scrimRef.current) return false;
          return !(node instanceof HTMLCanvasElement && (!node.width || !node.height));
        },
      });
      const image = new Image();
      image.src = data;
      await image.decode();
      if (generation !== captureGenerationRef.current) return;
      const scale = Math.min(1, MAX_SHOT_WIDTH / image.width, MAX_SHOT_HEIGHT / image.height);
      setScreenshot({ image, width: Math.round(image.width * scale), height: Math.round(image.height * scale) });
      setMarks([]);
      setStatus(t("report_bug_mark_hint"));
    } catch {
      if (generation === captureGenerationRef.current) setStatus(t("report_bug_capture_failed"));
    } finally {
      document.body.classList.remove("bug-report-capturing");
      if (generation === captureGenerationRef.current) setCapturing(false);
    }
  }

  function removeScreenshot() {
    captureGenerationRef.current++;
    dragStartRef.current = null;
    setScreenshot(null);
    setMarks([]);
    setPendingMark(null);
    setCapturing(false);
    setStatus("");
  }

  function downloadScreenshot() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // A fresh name per download keeps the browser from renaming it "(1)",
    // which would no longer match the name quoted in the issue.
    const name = screenshotFilename();
    downloadCanvas(canvas, name);
    setStatus(t("report_bug_downloaded").replace("{filename}", name));
  }

  function canvasPoint(event: PointerEvent<HTMLCanvasElement>) {
    const canvas = event.currentTarget;
    const box = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(canvas.width, ((event.clientX - box.left) * canvas.width) / box.width)),
      y: Math.max(0, Math.min(canvas.height, ((event.clientY - box.top) * canvas.height) / box.height)),
    };
  }

  function markTo(event: PointerEvent<HTMLCanvasElement>): Rect {
    const start = dragStartRef.current;
    const end = canvasPoint(event);
    return { x: start.x, y: start.y, width: end.x - start.x, height: end.y - start.y };
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const canvas = screenshot ? canvasRef.current : null;
    if (!canvas) {
      const url = buildIssueUrl(fields, context, null);
      if (!url) {
        setStatus(t("report_bug_too_long"));
        return;
      }
      openInNewTab(url);
      setStatus(t("report_bug_opened"));
      return;
    }
    // Both links are checked before touching the clipboard, so a too-long
    // report doesn't overwrite it for nothing.
    const name = screenshotFilename();
    const pasteUrl = buildIssueUrl(fields, context, PASTE_INSTRUCTIONS);
    const dropUrl = buildIssueUrl(fields, context, dropInstructions(name));
    if (!pasteUrl || !dropUrl) {
      setStatus(t("report_bug_too_long"));
      return;
    }
    // The issue tab opens only after the copy settles: the clipboard write
    // fails once this document loses focus to the new tab.
    setSubmitting(true);
    const copied = await copyCanvas(canvas);
    setSubmitting(false);
    if (!copied) downloadCanvas(canvas, name);
    openInNewTab(copied ? pasteUrl : dropUrl);
    setStatus(copied ? t("report_bug_opened_copied") : t("report_bug_opened_downloaded").replace("{filename}", name));
  }

  return (
    <div className="modal-scrim" ref={scrimRef}>
      <form
        className="modal-card bug-report-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bug-report-heading"
        onSubmit={submit}
      >
        <div className="modal-head">
          <h2 id="bug-report-heading">{t("report_bug")}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label={t("cancel")}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <p className="bug-report-intro">{t("report_bug_intro")}</p>

        <label className="bug-report-label" htmlFor="bug-report-title">{t("report_bug_title")}</label>
        <input
          id="bug-report-title"
          ref={titleRef}
          className="bug-report-input"
          type="text"
          maxLength={120}
          value={fields.title}
          onChange={(e) => updateField("title", e.target.value)}
        />
        <label className="bug-report-label" htmlFor="bug-report-description">{t("report_bug_description")}</label>
        <textarea
          id="bug-report-description"
          className="bug-report-input"
          maxLength={4000}
          value={fields.description}
          onChange={(e) => updateField("description", e.target.value)}
        />
        <label className="bug-report-label" htmlFor="bug-report-expected">{t("report_bug_expected")}</label>
        <textarea
          id="bug-report-expected"
          className="bug-report-input"
          maxLength={2000}
          value={fields.expected}
          onChange={(e) => updateField("expected", e.target.value)}
        />
        <label className="bug-report-label" htmlFor="bug-report-steps">{t("report_bug_steps")}</label>
        <textarea
          id="bug-report-steps"
          className="bug-report-input"
          maxLength={4000}
          value={fields.steps}
          onChange={(e) => updateField("steps", e.target.value)}
        />

        <details className="bug-report-details">
          <summary>{t("report_bug_details")}</summary>
          <pre>{JSON.stringify(context, null, 2)}</pre>
        </details>

        <div className="bug-report-actions">
          <button type="button" className="secondary-btn" disabled={capturing} onClick={capture}>
            {t("report_bug_capture")}
          </button>
        </div>

        {screenshot && (
          <div className="bug-report-screenshot">
            <canvas
              ref={canvasRef}
              width={screenshot.width}
              height={screenshot.height}
              aria-label={t("report_bug_mark_hint")}
              onPointerDown={(e) => {
                dragStartRef.current = canvasPoint(e);
                e.currentTarget.setPointerCapture(e.pointerId);
              }}
              onPointerMove={(e) => {
                if (dragStartRef.current) setPendingMark(markTo(e));
              }}
              onPointerUp={(e) => {
                if (!dragStartRef.current) return;
                const mark = markTo(e);
                if (Math.abs(mark.width) > 3 && Math.abs(mark.height) > 3) setMarks((prev) => [...prev, mark]);
                dragStartRef.current = null;
                setPendingMark(null);
              }}
              onPointerCancel={() => {
                dragStartRef.current = null;
                setPendingMark(null);
              }}
            />
            <p className="bug-report-note">{t("report_bug_attach_note")}</p>
            <div className="bug-report-actions">
              <button type="button" className="secondary-btn" onClick={downloadScreenshot}>
                {t("report_bug_download")}
              </button>
              <button type="button" className="secondary-btn" onClick={() => setMarks([])}>
                {t("report_bug_clear_marks")}
              </button>
              <button type="button" className="secondary-btn" onClick={removeScreenshot}>
                {t("report_bug_remove_screenshot")}
              </button>
            </div>
          </div>
        )}

        <p className="bug-report-status" role="status" aria-live="polite">{status}</p>

        <div className="bug-report-actions bug-report-footer">
          <button type="button" className="secondary-btn" onClick={onClose}>{t("cancel")}</button>
          <button type="submit" className="primary-btn" disabled={submitting}>{t("report_bug_open_issue")}</button>
        </div>
      </form>
    </div>
  );
}
