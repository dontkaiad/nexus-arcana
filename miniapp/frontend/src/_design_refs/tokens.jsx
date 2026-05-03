// tokens.jsx — Arcana palette, typography helpers, shared atoms

const ARCANA = {
  // Base night-mode (matches the screenshots)
  bgTop: '#0d1f1a',
  bgMid: '#0f1424',
  bgBot: '#1a0d2e',

  surface: 'rgba(15, 32, 28, 0.6)',     // dark green glass
  surfaceAlt: 'rgba(28, 18, 50, 0.55)', // purple glass
  border: 'rgba(140, 200, 180, 0.14)',
  borderStrong: 'rgba(180, 220, 200, 0.28)',

  text: '#e9efe8',
  textDim: 'rgba(220, 230, 220, 0.55)',
  textMute: 'rgba(180, 200, 190, 0.38)',

  // Brand greens (active tab / FAB)
  green: '#3ba087',
  greenDeep: '#1f6b58',
  greenSoft: 'rgba(59, 160, 135, 0.18)',

  // Purple (lower gradient)
  purple: '#6a4aa3',
  purpleDeep: '#2a1748',

  // Architect-only holo accents
  holoCyan: '#7bdfd1',
  holoMagenta: '#d97ec9',
  holoGold: '#e8c887',
  holoViolet: '#9d7be0',
};

const FONT_DISPLAY = '"Cormorant Garamond", "Cormorant", "Playfair Display", Georgia, serif';
const FONT_UI = '-apple-system, "SF Pro Text", "Inter", system-ui, sans-serif';
const FONT_MONO = '"JetBrains Mono", "SF Mono", ui-monospace, monospace';

// ─── Eye glyphs ─────────────────────────────────────────────────────────

function EyeOpen({ size = 28, color = '#e8c887', glow = true }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" style={{ overflow: 'visible' }}>
      {glow && (
        <defs>
          <filter id={`eye-glow-${size}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="1.2" />
          </filter>
        </defs>
      )}
      <path d="M2 16 Q16 4 30 16 Q16 28 2 16 Z" fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round"
            filter={glow ? `url(#eye-glow-${size})` : undefined} opacity="0.55" />
      <path d="M2 16 Q16 4 30 16 Q16 28 2 16 Z" fill="none" stroke={color} strokeWidth="1.1" strokeLinejoin="round" />
      <circle cx="16" cy="16" r="4.5" fill="none" stroke={color} strokeWidth="1.1" />
      <circle cx="16" cy="16" r="2" fill={color} />
      <circle cx="17.2" cy="14.8" r="0.6" fill="#fff" opacity="0.9" />
    </svg>
  );
}

function EyeMinimal({ size = 24, color = '#e8c887' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32">
      <ellipse cx="16" cy="16" rx="13" ry="6" fill="none" stroke={color} strokeWidth="1.2" />
      <circle cx="16" cy="16" r="3" fill={color} />
    </svg>
  );
}

function EyeRune({ size = 28, color = '#e8c887' }) {
  // triangle + eye (sigil-like)
  return (
    <svg width={size} height={size} viewBox="0 0 32 32">
      <path d="M16 4 L29 26 L3 26 Z" fill="none" stroke={color} strokeWidth="1.1" strokeLinejoin="round" opacity="0.7" />
      <ellipse cx="16" cy="19" rx="6" ry="3" fill="none" stroke={color} strokeWidth="1.1" />
      <circle cx="16" cy="19" r="1.6" fill={color} />
      {/* rays */}
      {[0,1,2,3,4].map(i => {
        const a = (-0.5 + i * 0.25) * Math.PI;
        const x1 = 16 + Math.cos(a) * 10;
        const y1 = 19 + Math.sin(a) * 10;
        const x2 = 16 + Math.cos(a) * 13;
        const y2 = 19 + Math.sin(a) * 13;
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth="0.8" opacity="0.6" />;
      })}
    </svg>
  );
}

function EyeSwitch({ kind, ...rest }) {
  if (kind === 'minimal') return <EyeMinimal {...rest} />;
  if (kind === 'rune') return <EyeRune {...rest} />;
  return <EyeOpen {...rest} />;
}

// ─── Star dust background ───────────────────────────────────────────────

function StarField({ count = 50, seed = 1, opacity = 0.7, animated = true }) {
  // deterministic pseudo-random
  const rng = (i) => {
    const x = Math.sin(i * 9301 + seed * 49297) * 233280;
    return x - Math.floor(x);
  };
  const stars = React.useMemo(() => Array.from({ length: count }, (_, i) => ({
    x: rng(i + 1) * 100,
    y: rng(i + 100) * 100,
    s: 0.3 + rng(i + 200) * 1.6,
    d: 2 + rng(i + 300) * 5,
    o: 0.2 + rng(i + 400) * 0.8,
  })), [count, seed]);

  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'hidden' }}>
      {stars.map((st, i) => (
        <div key={i} style={{
          position: 'absolute',
          left: `${st.x}%`,
          top: `${st.y}%`,
          width: st.s,
          height: st.s,
          borderRadius: '50%',
          background: '#fff',
          opacity: st.o * opacity,
          boxShadow: st.s > 1.2 ? `0 0 ${st.s * 2}px rgba(255,255,255,0.7)` : 'none',
          animation: animated ? `twinkle ${st.d}s ease-in-out ${rng(i + 500) * 4}s infinite` : 'none',
        }} />
      ))}
    </div>
  );
}

// ─── Architect ribbon badge ─────────────────────────────────────────────

function ArchitectBadge({ accent = ARCANA.holoGold, compact = false }) {
  return (
    <div style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      padding: compact ? '2px 8px' : '4px 10px',
      borderRadius: 999,
      fontFamily: FONT_MONO,
      fontSize: compact ? 8.5 : 9.5,
      letterSpacing: '0.18em',
      textTransform: 'uppercase',
      color: accent,
      background: `linear-gradient(90deg, ${accent}22, ${accent}08)`,
      border: `0.5px solid ${accent}55`,
      boxShadow: `0 0 12px ${accent}33, inset 0 0 8px ${accent}15`,
    }}>
      <svg width="9" height="9" viewBox="0 0 10 10">
        <path d="M5 0 L6 4 L10 5 L6 6 L5 10 L4 6 L0 5 L4 4 Z" fill={accent} />
      </svg>
      <span>архитектор</span>
    </div>
  );
}

Object.assign(window, {
  ARCANA, FONT_DISPLAY, FONT_UI, FONT_MONO,
  EyeOpen, EyeMinimal, EyeRune, EyeSwitch,
  StarField, ArchitectBadge,
});
