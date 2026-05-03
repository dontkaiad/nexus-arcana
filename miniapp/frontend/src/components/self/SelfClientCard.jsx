// SelfClientCard.jsx — THE ONE design for the self-client (Кай).
// Origin: design refs at miniapp/frontend/src/_design_refs/{tokens,variants}.jsx.
// Only used when client.self === true. Other clients keep the regular look.

import React, { useEffect, useRef, useState } from "react";

export const ARCANA_HOLO = {
  cyan: "#7bdfd1",
  magenta: "#d97ec9",
  gold: "#e8c887",
  violet: "#9d7be0",
};

const FONT_DISPLAY = 'var(--f-display)';
const FONT_MONO = 'var(--f-mono)';
const FONT_UI = 'var(--f-body)';

// ────────────────────────────────────────────────────────────────────────
// ArchitectBadge — pill «+ архитектор»
// ────────────────────────────────────────────────────────────────────────

export function ArchitectBadge({ accent = ARCANA_HOLO.gold, compact = false }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: compact ? "2px 8px" : "3px 10px",
      borderRadius: 999,
      fontFamily: FONT_MONO,
      fontSize: compact ? 8.5 : 9.5,
      letterSpacing: "0.2em",
      textTransform: "uppercase",
      color: accent,
      background: `linear-gradient(90deg, ${accent}22, ${accent}08)`,
      border: `0.5px solid ${accent}55`,
      boxShadow: `0 0 12px ${accent}33, inset 0 0 8px ${accent}15`,
      whiteSpace: "nowrap",
    }}>
      <svg width="9" height="9" viewBox="0 0 10 10">
        <path d="M5 0 L6 4 L10 5 L6 6 L5 10 L4 6 L0 5 L4 4 Z" fill={accent} />
      </svg>
      <span>{compact ? "архитектор" : "Архитектор"}</span>
    </span>
  );
}

// ────────────────────────────────────────────────────────────────────────
// ClampedLivingEye — eye whose iris follows the pointer, clipped by the lid.
// Mobile-safe: without mousemove the iris stays centered.
// ────────────────────────────────────────────────────────────────────────

export function ClampedLivingEye({
  size = 80,
  accent = ARCANA_HOLO.cyan,
  animated = true,
  bgFill = "#0a0a18",
}) {
  const ref = useRef(null);
  const [pupil, setPupil] = useState({ x: 0, y: 0 });

  useEffect(() => {
    if (!animated) { setPupil({ x: 0, y: 0 }); return; }
    const handler = (e) => {
      if (!ref.current) return;
      const r = ref.current.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const dx = e.clientX - cx;
      const dy = e.clientY - cy;
      const len = Math.hypot(dx, dy) || 1;
      const k = Math.min(1, len / 180);
      setPupil({ x: (dx / len) * 22 * k, y: (dy / len) * 14 * k });
    };
    window.addEventListener("mousemove", handler);
    return () => window.removeEventListener("mousemove", handler);
  }, [animated]);

  const uid = `eye-${size}-${accent.replace("#", "")}`;
  const isTransparent = bgFill === "transparent";
  return (
    <div ref={ref} style={{ width: size, height: size * 0.7, position: "relative" }}>
      <svg width={size} height={size * 0.7} viewBox="0 0 100 70" style={{ overflow: "visible" }}>
        <defs>
          <radialGradient id={`iris-${uid}`} cx="50%" cy="50%">
            <stop offset="0%" stopColor={accent} stopOpacity="1" />
            <stop offset="55%" stopColor={accent} stopOpacity="0.85" />
            <stop offset="100%" stopColor="#1a0d2e" stopOpacity="1" />
          </radialGradient>
          <radialGradient id={`glow-${uid}`} cx="50%" cy="50%">
            <stop offset="0%" stopColor={accent} stopOpacity="0.55" />
            <stop offset="100%" stopColor={accent} stopOpacity="0" />
          </radialGradient>
          <clipPath id={`clip-${uid}`}>
            <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" />
          </clipPath>
        </defs>
        {!isTransparent && (
          <ellipse cx="50" cy="35" rx="55" ry="35" fill={`url(#glow-${uid})`}>
            {animated && <animate attributeName="opacity" values="0.5;0.9;0.5" dur="3.5s" repeatCount="indefinite" />}
          </ellipse>
        )}
        {!isTransparent && (
          <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" fill={bgFill} />
        )}
        <g clipPath={`url(#clip-${uid})`}>
          <g
            transform={`translate(${pupil.x}, ${pupil.y})`}
            style={{ transition: animated ? "transform 0.18s ease-out" : "none" }}
          >
            <circle cx="50" cy="35" r="18" fill={`url(#iris-${uid})`} />
            <circle cx="50" cy="35" r="18" fill="none" stroke={accent} strokeWidth="0.6" opacity="0.7" />
            {Array.from({ length: 14 }, (_, i) => {
              const a = (i / 14) * Math.PI * 2;
              return (
                <line
                  key={i}
                  x1={50 + Math.cos(a) * 9} y1={35 + Math.sin(a) * 9}
                  x2={50 + Math.cos(a) * 17} y2={35 + Math.sin(a) * 17}
                  stroke={accent} strokeWidth="0.4" opacity="0.55"
                />
              );
            })}
            <circle cx="50" cy="35" r="9" fill="#000" />
            <circle cx="53" cy="32.5" r="2.2" fill="#fff" opacity="0.9" />
          </g>
        </g>
        <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" fill="none" stroke={accent} strokeWidth="0.9" />
        <path d="M8 33 Q50 0 92 33" fill="none" stroke="#fff" strokeWidth="0.4" opacity="0.35" />
      </svg>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// V6OneAvatar — bare living eye, no frame (44px slot in a list row)
// ────────────────────────────────────────────────────────────────────────

export function SelfAvatar({ size = 44, animated = true, accent = ARCANA_HOLO.cyan }) {
  return (
    <div style={{
      width: size, height: size,
      display: "flex", alignItems: "center", justifyContent: "center",
      position: "relative",
    }}>
      <ClampedLivingEye size={size * 1.5} animated={animated} accent={accent} bgFill="transparent" />
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// V6OneAvatarBig — aurora ring + holo cone + giant living eye
// ────────────────────────────────────────────────────────────────────────

export function SelfAvatarBig({ size = 110, animated = true, accent = ARCANA_HOLO.cyan }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: "50%",
      position: "relative",
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div style={{
        position: "absolute", inset: -8, borderRadius: "50%",
        background: `radial-gradient(circle, ${accent}88, ${ARCANA_HOLO.magenta}55, transparent 70%)`,
        filter: "blur(10px)",
        animation: animated ? "aurora-breathe 3.5s ease-in-out infinite" : "none",
      }} />
      <div style={{
        position: "absolute", inset: 0, borderRadius: "50%",
        background: `conic-gradient(from 90deg, ${ARCANA_HOLO.cyan}, ${ARCANA_HOLO.magenta}, ${ARCANA_HOLO.gold}, ${ARCANA_HOLO.violet}, ${ARCANA_HOLO.cyan})`,
        animation: animated ? "holo-spin 8s linear infinite" : "none",
        opacity: 0.95,
      }} />
      <div style={{
        position: "absolute", inset: 3, borderRadius: "50%",
        background: "radial-gradient(circle at 50% 40%, #1a1530, #0a0a18)",
        display: "flex", alignItems: "center", justifyContent: "center",
        overflow: "hidden",
      }}>
        <ClampedLivingEye size={size * 0.78} animated={animated} accent={accent} bgFill="#0a0a18" />
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// SelfListCard — holo-foil wrapper for the list row.
// In the real list keep it tight; the Architect badge lives in the detail.
// ────────────────────────────────────────────────────────────────────────

export function SelfListCard({ client, onClick }) {
  return (
    <div
      onClick={onClick}
      style={{
        position: "relative",
        padding: "12px 14px",
        marginBottom: 4,
        borderRadius: 14,
        background: "linear-gradient(135deg, rgba(13,31,26,0.92), rgba(26,13,46,0.92))",
        overflow: "hidden",
        cursor: onClick ? "pointer" : "default",
      }}
    >
      <div style={{
        position: "absolute", inset: 0, borderRadius: 14, padding: 1,
        background: `linear-gradient(120deg, ${ARCANA_HOLO.cyan}, ${ARCANA_HOLO.magenta}, ${ARCANA_HOLO.gold}, ${ARCANA_HOLO.violet}, ${ARCANA_HOLO.cyan})`,
        backgroundSize: "300% 100%",
        WebkitMask: "linear-gradient(#fff,#fff) content-box, linear-gradient(#fff,#fff)",
        WebkitMaskComposite: "xor",
        maskComposite: "exclude",
        animation: "holo-border 4s linear infinite",
        opacity: 0.9,
        pointerEvents: "none",
      }} />
      <div style={{
        position: "absolute", inset: 0,
        background: "linear-gradient(115deg, transparent 35%, rgba(255,255,255,0.08) 50%, transparent 65%)",
        animation: "holo-shine 5s ease-in-out infinite",
        pointerEvents: "none",
      }} />
      <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 12 }}>
        <SelfAvatar size={44} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <span style={{
              fontFamily: FONT_DISPLAY, fontSize: 19, fontStyle: "italic",
              color: "#e9efe8", lineHeight: 1.1,
            }}>
              {client.name || "Кай"}
            </span>
            <ArchitectBadge accent={ARCANA_HOLO.gold} compact />
          </div>
          <div style={{
            fontFamily: FONT_MONO, fontSize: 10.5, color: "rgba(220,230,220,0.55)",
            marginTop: 3, letterSpacing: "0.05em",
          }}>
            {client.sessions ?? 0} сеансов · {client.rituals ?? 0} ритуалов
          </div>
        </div>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// SelfDetailHeader — header for the bottom-sheet client detail.
// Compact size (90px eye) so it fits inside the sheet without overwhelming.
// ────────────────────────────────────────────────────────────────────────

function SelfPhoto({ url }) {
  const [broken, setBroken] = React.useState(false);
  if (!url || broken) return null;
  return (
    <div style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}>
      <img
        src={url}
        alt="Кай"
        onError={() => setBroken(true)}
        style={{
          width: 50, height: 50, borderRadius: "50%",
          objectFit: "cover",
          border: `2px solid ${ARCANA_HOLO.gold}`,
          boxShadow: `0 0 14px ${ARCANA_HOLO.gold}55`,
        }}
      />
    </div>
  );
}

export function SelfDetailHeader({ client }) {
  const contactItems = (client.contact || "")
    .split(/[,;\n]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  const handles = contactItems.filter((x) => x.startsWith("@"));
  const phones = contactItems.filter((x) => !x.startsWith("@"));
  return (
    <div style={{
      position: "relative", padding: 18, borderRadius: 18,
      background: "linear-gradient(160deg, rgba(13,31,26,0.95), rgba(26,13,46,0.95))",
      overflow: "hidden",
      marginBottom: 16,
    }}>
      <div style={{
        position: "absolute", inset: 0, borderRadius: 18, padding: 1.2,
        background: `linear-gradient(120deg, ${ARCANA_HOLO.cyan}, ${ARCANA_HOLO.magenta}, ${ARCANA_HOLO.gold}, ${ARCANA_HOLO.violet}, ${ARCANA_HOLO.cyan})`,
        backgroundSize: "300% 100%",
        WebkitMask: "linear-gradient(#fff,#fff) content-box, linear-gradient(#fff,#fff)",
        WebkitMaskComposite: "xor",
        maskComposite: "exclude",
        animation: "holo-border 5s linear infinite",
        pointerEvents: "none",
      }} />
      <div style={{
        position: "absolute", inset: 0,
        background: "linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.07) 50%, transparent 70%)",
        animation: "holo-shine 6s ease-in-out infinite",
        pointerEvents: "none",
      }} />
      {/* sigil background — geometric grid + concentric shapes (ported from V6OneDetailCard) */}
      <div style={{ position: "absolute", inset: 0, overflow: "hidden", pointerEvents: "none" }}>
        <div style={{
          position: "absolute", inset: 0,
          backgroundImage: `linear-gradient(${ARCANA_HOLO.cyan}10 1px, transparent 1px),
                            linear-gradient(90deg, ${ARCANA_HOLO.cyan}10 1px, transparent 1px)`,
          backgroundSize: "24px 24px",
          maskImage: "radial-gradient(circle at 50% 32%, black 30%, transparent 75%)",
          WebkitMaskImage: "radial-gradient(circle at 50% 32%, black 30%, transparent 75%)",
        }} />
      </div>
      <div style={{ position: "relative", textAlign: "center" }}>
        <SelfPhoto url={client.photo_url} />
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginBottom: 6,
        }}>
          <span style={{
            fontFamily: FONT_MONO, fontSize: 9, color: ARCANA_HOLO.cyan,
            letterSpacing: "0.25em",
          }}>FOIL · 001/001</span>
          <ArchitectBadge accent={ARCANA_HOLO.gold} compact />
        </div>
        <div style={{ display: "flex", justifyContent: "center", padding: "14px 0 6px", position: "relative" }}>
          <svg style={{
            position: "absolute", left: "50%", top: "50%",
            transform: "translate(-50%, -50%)",
            width: 210, height: 210, pointerEvents: "none",
            filter: `drop-shadow(0 0 4px ${ARCANA_HOLO.gold}66)`,
          }} viewBox="0 0 320 320">
            <g stroke={`${ARCANA_HOLO.gold}b0`} fill="none" strokeWidth="1.2">
              <circle cx="160" cy="160" r="150" />
              <circle cx="160" cy="160" r="120" strokeDasharray="3 5" />
              <polygon points="160,30 272,224 48,224" />
              <polygon points="160,290 48,96 272,96" opacity="0.7" />
            </g>
          </svg>
          <ClampedLivingEye size={150} accent={ARCANA_HOLO.cyan} bgFill="#0a0e18" />
        </div>
        <div style={{
          fontFamily: FONT_DISPLAY, fontSize: 38, fontStyle: "italic",
          color: "#f2eee8", lineHeight: 1, marginTop: 6,
        }}>
          {client.name || "Кай"}
        </div>
        <div style={{
          fontFamily: FONT_MONO, fontSize: 9.5, color: ARCANA_HOLO.cyan,
          marginTop: 7, letterSpacing: "0.32em", textTransform: "uppercase", opacity: 0.9,
        }}>
          архитектор · ведающая
        </div>
        {(handles.length > 0 || phones.length > 0) && (
          <>
            <div style={{
              margin: "16px auto 0", maxWidth: 240, height: 1,
              background: `linear-gradient(90deg, transparent, ${ARCANA_HOLO.magenta}66, transparent)`,
            }} />
            <div style={{
              marginTop: 14, padding: "12px 14px", borderRadius: 12,
              background: "rgba(0,0,0,0.28)",
              border: `0.5px solid rgba(140,200,180,0.18)`,
              textAlign: "left",
            }}>
              <div style={{
                fontFamily: FONT_UI, fontSize: 12,
                color: "rgba(220,230,220,0.7)", lineHeight: 1.7,
              }}>
                {handles.length > 0 && (
                  <div>
                    𝕏 {handles.join(" · ")}
                  </div>
                )}
                {phones.map((p, i) => (
                  <div key={i} style={{ fontFamily: FONT_MONO, fontSize: 11 }}>{p}</div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
