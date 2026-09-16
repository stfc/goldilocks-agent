import { useEffect, useRef, useState } from "react";
import "../../node_modules/weas/dist/style.css";
import {
  WEAS_SUPPORTED_EXTS,
  formatStructureLabel,
  normalizeStructureExtension,
  parsePoscarToPayload,
} from "../utils/structureFiles.js";

function getErrorMessage(error, ext) {
  const normalized = normalizeStructureExtension(ext);
  if (!WEAS_SUPPORTED_EXTS.has(normalized)) {
    return `${formatStructureLabel(ext)} is not supported by the WEAS viewer. Try CIF, XYZ, XSF, CUBE, POSCAR, or VASP.`;
  }
  return error instanceof Error && error.message
    ? error.message
    : `Failed to load ${formatStructureLabel(ext)} in the WEAS viewer.`;
}

function parseStructurePayload(source, helpers) {
  const normalizedExt = normalizeStructureExtension(source.ext);

  if (normalizedExt === ".weas-json") return JSON.parse(source.content);
  if (normalizedExt === ".cif") return helpers.parseCIF(source.content);
  if (normalizedExt === ".xyz") return helpers.parseXYZ(source.content);
  if (normalizedExt === ".xsf") return helpers.parseXSF(source.content);
  if (normalizedExt === ".cube") return helpers.parseCube(source.content);
  if (normalizedExt === ".poscar" || normalizedExt === ".vasp") return parsePoscarToPayload(source.content);

  throw new Error(`Unsupported file extension: ${normalizedExt}`);
}

export default function WeasStructureViewport({ source, height = 320 }) {
  const hostRef = useRef(null);
  const viewerRef = useRef(null);
  const helpersRef = useRef(null);
  const [isBooting, setIsBooting] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const [viewerError, setViewerError] = useState("");
  const [legendEntries, setLegendEntries] = useState([]);

  useEffect(() => {
    let cancelled = false;
    const hostElement = hostRef.current;

    async function bootViewer() {
      if (viewerRef.current || !hostElement) return;

      setIsBooting(true);
      try {
        const mod = await import("weas");
        if (cancelled || !hostElement) return;

        helpersRef.current = {
          applyStructurePayload: mod.applyStructurePayload,
          parseCIF: mod.parseCIF,
          parseCube: mod.parseCube,
          parseXSF: mod.parseXSF,
          parseXYZ: mod.parseXYZ,
        };
        viewerRef.current = new mod.WEAS({
          domElement: hostElement,
          viewerConfig: { atomScale: 0.8 },
          guiConfig: { atomLegend: { enabled: false } },
        });
        viewerRef.current.avr.modelStyle = 1;
        setIsReady(true);
      } catch (loadError) {
        if (!cancelled) {
          setViewerError("Failed to load the WEAS viewer.");
          console.error("Failed to initialize WEAS.", loadError);
        }
      } finally {
        if (!cancelled) setIsBooting(false);
      }
    }

    bootViewer();

    return () => {
      cancelled = true;
      try {
        viewerRef.current?.clear();
      } catch (cleanupError) {
        console.error("Failed to clean up WEAS.", cleanupError);
      }
      viewerRef.current = null;
      helpersRef.current = null;
      if (hostElement) hostElement.innerHTML = "";
    };
  }, []);

  useEffect(() => {
    if (!isReady || !viewerRef.current || !helpersRef.current) return;

    let cancelled = false;

    async function syncStructure() {
      if (!source) {
        try {
          viewerRef.current.clear();
        } catch (clearError) {
          console.error("Failed to clear WEAS.", clearError);
        }
        if (!cancelled) { setViewerError(""); setLegendEntries([]); }
        return;
      }

      const normalizedExt = normalizeStructureExtension(source.ext);
      if (!WEAS_SUPPORTED_EXTS.has(normalizedExt)) {
        if (!cancelled) setViewerError("");
        return;
      }

      try {
        const payload = parseStructurePayload(source, helpersRef.current);
        helpersRef.current.applyStructurePayload(viewerRef.current, payload);
        viewerRef.current.render();
        if (!cancelled) {
          setViewerError("");
          const settings = viewerRef.current?.avr?.atomManager?.settings ?? {};
          const entries = Object.entries(settings).map(([symbol, data]) => ({
            symbol,
            color: data.color ? `#${data.color.getHexString()}` : "#888",
          }));
          setLegendEntries(entries);
        }
      } catch (parseError) {
        console.error("Failed to render structure in WEAS.", parseError);
        if (!cancelled) { setViewerError(getErrorMessage(parseError, source.ext)); setLegendEntries([]); }
      }
    }

    syncStructure();

    return () => {
      cancelled = true;
    };
  }, [isReady, source]);

  const unsupportedMessage = source && !WEAS_SUPPORTED_EXTS.has(normalizeStructureExtension(source.ext))
    ? getErrorMessage(null, source.ext)
    : "";
  const overlayMessage = viewerError || unsupportedMessage;
  const showEmptyState = !source;
  const showOverlay = Boolean(showEmptyState || overlayMessage || isBooting || !isReady);
  const overlayIcon = showEmptyState ? "⬆" : overlayMessage ? "🔬" : "";
  const overlayTitle = showEmptyState
    ? "No structure loaded"
    : overlayMessage
      ? "Viewer unavailable"
      : "Loading WEAS viewer…";
  const overlayCopy = showEmptyState
    ? (
        <>
          Drag and drop a structure file onto the chat, or use the <strong>📎</strong> button to attach one.
        </>
      )
    : overlayMessage;
  const overlayHint = showEmptyState ? "Supported here: .cif · .xyz · .xsf · .cube · .poscar · .vasp" : "";

  return (
    <div style={viewportWrapStyle(height)}>
      <div ref={hostRef} style={hostStyle} />
      {legendEntries.length > 0 && !showOverlay && (
        <div style={legendStyle}>
          {legendEntries.map(({ symbol, color }) => (
            <div key={symbol} style={legendRowStyle}>
              <span style={{ ...legendDotStyle, background: color }} />
              <span style={legendLabelStyle}>{symbol}</span>
            </div>
          ))}
        </div>
      )}
      {showOverlay && (
        <div style={loadingOverlayStyle}>
          <div style={overlayContentStyle}>
            {overlayIcon ? <div style={iconStyle}>{overlayIcon}</div> : null}
            <div style={titleStyle}>{overlayTitle}</div>
            {overlayCopy ? <div style={copyStyle}>{overlayCopy}</div> : null}
            {overlayHint ? <div style={hintStyle}>{overlayHint}</div> : null}
          </div>
        </div>
      )}
    </div>
  );
}

const viewportWrapStyle = (height) => ({
  position: "relative",
  width: "100%",
  flex: "1 1 0",
  minWidth: 320,
  minHeight: height,
  borderRadius: 18,
  overflow: "hidden",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  background: "#ffffff",
  boxShadow: "inset 0 1px 0 rgba(255,255,255,0.8)",
});

const hostStyle = {
  display: "block",
  width: "100%",
  height: "100%",
  minHeight: 260,
};

const loadingOverlayStyle = {
  position: "absolute",
  inset: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(248, 250, 252, 0.82)",
  backdropFilter: "blur(4px)",
};

const overlayContentStyle = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 10,
  padding: 24,
  textAlign: "center",
};

const iconStyle = {
  fontSize: 28,
  lineHeight: 1,
};

const titleStyle = {
  fontSize: 15,
  fontWeight: 600,
  color: "var(--text, #0f172a)",
};

const copyStyle = {
  maxWidth: 360,
  fontSize: 12,
  lineHeight: 1.7,
  color: "var(--muted, #475569)",
};

const hintStyle = {
  fontSize: 11,
  color: "var(--subtle, #64748b)",
};

const legendStyle = {
  position: "absolute",
  bottom: 12,
  right: 12,
  display: "flex",
  flexDirection: "column",
  gap: 4,
  padding: "8px 10px",
  background: "rgba(255,255,255,0.82)",
  backdropFilter: "blur(4px)",
  borderRadius: 8,
  border: "1px solid rgba(148,163,184,0.2)",
  pointerEvents: "none",
};

const legendRowStyle = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const legendDotStyle = {
  display: "inline-block",
  width: 10,
  height: 10,
  borderRadius: "50%",
  flexShrink: 0,
};

const legendLabelStyle = {
  fontSize: 13,
  fontWeight: 500,
  color: "#1e293b",
  fontFamily: "system-ui, sans-serif",
};
