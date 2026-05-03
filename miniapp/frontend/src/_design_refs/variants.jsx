// variants.jsx — 5 elite "Architect" card variants for Arcana
// Each variant exposes: <V*Avatar/>, <V*ListCard/>, <V*DetailCard/>

// ═══════════════════════════════════════════════════════════════════════
// VARIANT 1 — HOLO CARD (refractive holographic foil, like a rare TCG)
// ═══════════════════════════════════════════════════════════════════════

function V1HoloAvatar({ size = 44, eye = 'open', animated = true }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 12,
      position: 'relative', overflow: 'hidden',
      background: '#0d1f1a',
      border: '0.5px solid rgba(255,255,255,0.15)',
    }}>
      <div style={{
        position: 'absolute', inset: -2,
        background: `conic-gradient(from 0deg,
          ${ARCANA.holoCyan}, ${ARCANA.holoMagenta}, ${ARCANA.holoGold},
          ${ARCANA.holoViolet}, ${ARCANA.holoCyan})`,
        opacity: 0.5,
        filter: 'blur(6px)',
        animation: animated ? 'holo-spin 6s linear infinite' : 'none',
      }} />
      <div style={{
        position: 'absolute', inset: 1, borderRadius: 11,
        background: 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.08), transparent 60%), #0d1f1a',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <EyeSwitch kind={eye} size={size * 0.55} color={ARCANA.holoGold} />
      </div>
      {/* sweeping shine */}
      {animated && <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.25) 50%, transparent 70%)',
        animation: 'holo-shine 3.5s ease-in-out infinite',
        mixBlendMode: 'overlay',
      }} />}
    </div>
  );
}

function V1HoloListCard({ animated, eye }) {
  return (
    <div style={{
      position: 'relative',
      padding: '14px 16px',
      borderRadius: 14,
      background: 'linear-gradient(135deg, rgba(13,31,26,0.9), rgba(26,13,46,0.9))',
      border: '0.5px solid transparent',
      backgroundClip: 'padding-box',
      overflow: 'hidden',
    }}>
      {/* holographic border */}
      <div style={{
        position: 'absolute', inset: 0, borderRadius: 14, padding: 1,
        background: `linear-gradient(120deg,
          ${ARCANA.holoCyan}, ${ARCANA.holoMagenta}, ${ARCANA.holoGold}, ${ARCANA.holoViolet}, ${ARCANA.holoCyan})`,
        backgroundSize: '300% 100%',
        WebkitMask: 'linear-gradient(#fff,#fff) content-box, linear-gradient(#fff,#fff)',
        WebkitMaskComposite: 'xor', maskComposite: 'exclude',
        animation: animated ? 'holo-border 4s linear infinite' : 'none',
        opacity: 0.85,
      }} />
      {/* refractive sheen */}
      {animated && <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(115deg, transparent 35%, rgba(255,255,255,0.08) 50%, transparent 65%)',
        animation: 'holo-shine 5s ease-in-out infinite',
        pointerEvents: 'none',
      }} />}
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
        <V1HoloAvatar size={44} eye={eye} animated={animated} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 19, fontStyle: 'italic', color: ARCANA.text, letterSpacing: '0.01em' }}>Кай</span>
            <ArchitectBadge accent={ARCANA.holoGold} compact />
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: ARCANA.textDim, marginTop: 3, letterSpacing: '0.05em' }}>
            1 сеанс · 0 ритуалов
          </div>
        </div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 9, color: ARCANA.holoGold, letterSpacing: '0.2em', writingMode: 'vertical-rl', transform: 'rotate(180deg)', opacity: 0.7 }}>
          001/001
        </div>
      </div>
    </div>
  );
}

function V1HoloDetailCard({ animated, eye }) {
  return (
    <div style={{
      position: 'relative', padding: 18, borderRadius: 18,
      background: 'linear-gradient(160deg, rgba(13,31,26,0.92), rgba(26,13,46,0.92))',
      overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', inset: 0, borderRadius: 18, padding: 1.2,
        background: `linear-gradient(120deg,
          ${ARCANA.holoCyan}, ${ARCANA.holoMagenta}, ${ARCANA.holoGold}, ${ARCANA.holoViolet}, ${ARCANA.holoCyan})`,
        backgroundSize: '300% 100%',
        WebkitMask: 'linear-gradient(#fff,#fff) content-box, linear-gradient(#fff,#fff)',
        WebkitMaskComposite: 'xor', maskComposite: 'exclude',
        animation: animated ? 'holo-border 5s linear infinite' : 'none',
      }} />
      {animated && <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.07) 50%, transparent 70%)',
        animation: 'holo-shine 6s ease-in-out infinite',
      }} />}
      <div style={{ position: 'relative' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
          <span style={{ fontFamily: FONT_MONO, fontSize: 9, color: ARCANA.holoCyan, letterSpacing: '0.25em' }}>FOIL · 001/001</span>
          <ArchitectBadge accent={ARCANA.holoGold} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <V1HoloAvatar size={68} eye={eye} animated={animated} />
          <div>
            <div style={{ fontFamily: FONT_DISPLAY, fontSize: 32, fontStyle: 'italic', color: ARCANA.text, lineHeight: 1, letterSpacing: '0.005em' }}>Кай</div>
            <div style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: ARCANA.holoCyan, marginTop: 4, letterSpacing: '0.2em', textTransform: 'uppercase' }}>создатель · я</div>
          </div>
        </div>
        <div style={{ height: 1, background: `linear-gradient(90deg, transparent, ${ARCANA.holoMagenta}55, transparent)`, margin: '14px 0' }} />
        <div style={{ display: 'grid', gap: 6, fontFamily: FONT_UI, fontSize: 12, color: ARCANA.textDim }}>
          <div>𝕏 @dontplayad</div>
          <div>𝕏 @hey_lark</div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 11 }}>+7 961 931 12 60</div>
          <div style={{ marginTop: 2, fontStyle: 'italic', color: ARCANA.holoGold, opacity: 0.8 }}>с начала всего</div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// VARIANT 2 — CONSTELLATION (eye drawn from interactive stars)
// ═══════════════════════════════════════════════════════════════════════

function ConstellationEye({ size = 80, animated, accent = ARCANA.holoGold }) {
  // 7 stars forming an eye shape + pupil
  const stars = [
    { x: 10, y: 50, m: 0.6 },
    { x: 28, y: 32, m: 0.8 },
    { x: 50, y: 22, m: 1.2 }, // top
    { x: 72, y: 32, m: 0.8 },
    { x: 90, y: 50, m: 0.6 },
    { x: 50, y: 72, m: 1.0 }, // bottom
    { x: 50, y: 50, m: 1.4 }, // pupil
  ];
  const lines = [[0,1],[1,2],[2,3],[3,4],[4,5],[5,0],[6,2],[6,5]];
  return (
    <svg width={size} height={size * 0.7} viewBox="0 0 100 80" style={{ overflow: 'visible' }}>
      {lines.map(([a, b], i) => (
        <line key={i} x1={stars[a].x} y1={stars[a].y} x2={stars[b].x} y2={stars[b].y}
              stroke={accent} strokeWidth="0.4" opacity="0.4"
              strokeDasharray="1 2" />
      ))}
      {stars.map((s, i) => (
        <g key={i} style={{
          animation: animated ? `star-pulse ${2 + i * 0.3}s ease-in-out ${i * 0.2}s infinite` : 'none',
          transformOrigin: `${s.x}px ${s.y}px`,
        }}>
          <circle cx={s.x} cy={s.y} r={s.m * 2.5} fill={accent} opacity="0.25" />
          <circle cx={s.x} cy={s.y} r={s.m * 1.1} fill="#fff" />
        </g>
      ))}
    </svg>
  );
}

function V2ConstAvatar({ size = 44, animated }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 12,
      background: 'radial-gradient(circle at 50% 50%, #1a2540, #0a0f1c)',
      border: `0.5px solid ${ARCANA.holoGold}55`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      position: 'relative', overflow: 'hidden',
    }}>
      <StarField count={12} seed={3} opacity={0.5} animated={animated} />
      <div style={{ position: 'relative', transform: 'scale(0.85)' }}>
        <ConstellationEye size={size * 0.85} animated={animated} />
      </div>
    </div>
  );
}

function V2ConstListCard({ animated }) {
  return (
    <div style={{
      position: 'relative', padding: '14px 16px', borderRadius: 14,
      background: 'linear-gradient(180deg, rgba(10,18,32,0.85), rgba(20,12,38,0.85))',
      border: `0.5px solid ${ARCANA.holoGold}40`,
      overflow: 'hidden',
      boxShadow: `0 0 0 1px ${ARCANA.holoGold}10, 0 8px 30px rgba(0,0,0,0.4)`,
    }}>
      <StarField count={20} seed={2} opacity={0.6} animated={animated} />
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
        <V2ConstAvatar animated={animated} />
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 19, fontStyle: 'italic', color: ARCANA.text }}>Кай</span>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 13, fontStyle: 'italic', color: ARCANA.holoGold, opacity: 0.8 }}>· я</span>
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: ARCANA.textDim, marginTop: 3 }}>1 сеанс · 0 ритуалов</div>
        </div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 8.5, color: ARCANA.holoGold, letterSpacing: '0.2em', opacity: 0.7, textAlign: 'right' }}>
          α<br/>arch.
        </div>
      </div>
    </div>
  );
}

function V2ConstDetailCard({ animated }) {
  return (
    <div style={{
      position: 'relative', padding: 18, borderRadius: 18,
      background: 'radial-gradient(ellipse at 30% 0%, rgba(60,40,100,0.55), rgba(8,14,26,0.95) 70%)',
      border: `0.5px solid ${ARCANA.holoGold}40`,
      overflow: 'hidden',
      minHeight: 280,
    }}>
      <StarField count={60} seed={5} opacity={0.75} animated={animated} />
      <div style={{ position: 'relative' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span style={{ fontFamily: FONT_MONO, fontSize: 9, color: ARCANA.holoGold, letterSpacing: '0.25em' }}>СОЗВЕЗДИЕ · α</span>
          <ArchitectBadge accent={ARCANA.holoGold} compact />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '10px 0 14px' }}>
          <ConstellationEye size={140} animated={animated} />
        </div>
        <div style={{ textAlign: 'center', fontFamily: FONT_DISPLAY, fontSize: 36, fontStyle: 'italic', color: ARCANA.text, lineHeight: 1 }}>Кай</div>
        <div style={{ textAlign: 'center', fontFamily: FONT_MONO, fontSize: 9.5, color: ARCANA.holoGold, marginTop: 6, letterSpacing: '0.3em', textTransform: 'uppercase', opacity: 0.85 }}>
          архитектор · α persei
        </div>
        <div style={{ marginTop: 14, padding: '10px 12px', borderRadius: 10, background: 'rgba(255,255,255,0.03)', border: `0.5px solid ${ARCANA.border}` }}>
          <div style={{ fontFamily: FONT_UI, fontSize: 11.5, color: ARCANA.textDim, lineHeight: 1.6 }}>
            𝕏 @dontplayad · @hey_lark<br/>
            <span style={{ fontFamily: FONT_MONO }}>+7 961 931 12 60</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// VARIANT 3 — AURORA GLOW (breathing aura, soft luminous gradient)
// ═══════════════════════════════════════════════════════════════════════

function V3AuroraAvatar({ size = 44, eye = 'open', animated }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      position: 'relative',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        position: 'absolute', inset: -4, borderRadius: '50%',
        background: `radial-gradient(circle, ${ARCANA.holoMagenta}88, ${ARCANA.holoCyan}55, transparent 70%)`,
        filter: 'blur(6px)',
        animation: animated ? 'aurora-breathe 3.5s ease-in-out infinite' : 'none',
      }} />
      <div style={{
        position: 'absolute', inset: 0, borderRadius: '50%',
        background: `conic-gradient(from 90deg, ${ARCANA.holoCyan}, ${ARCANA.holoMagenta}, ${ARCANA.holoGold}, ${ARCANA.holoCyan})`,
        animation: animated ? 'holo-spin 8s linear infinite' : 'none',
        opacity: 0.9,
      }} />
      <div style={{
        position: 'absolute', inset: 2, borderRadius: '50%',
        background: '#0d1f1a',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <EyeSwitch kind={eye} size={size * 0.5} color={ARCANA.holoGold} />
      </div>
    </div>
  );
}

function V3AuroraListCard({ animated, eye }) {
  return (
    <div style={{
      position: 'relative', padding: '14px 16px', borderRadius: 14,
      background: 'linear-gradient(135deg, rgba(13,31,26,0.85), rgba(26,13,46,0.85))',
      border: `0.5px solid ${ARCANA.borderStrong}`,
      overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', left: -40, top: -40, width: 120, height: 120, borderRadius: '50%',
        background: `radial-gradient(circle, ${ARCANA.holoMagenta}55, transparent 70%)`,
        filter: 'blur(20px)',
        animation: animated ? 'aurora-drift 6s ease-in-out infinite' : 'none',
      }} />
      <div style={{
        position: 'absolute', right: -40, bottom: -40, width: 120, height: 120, borderRadius: '50%',
        background: `radial-gradient(circle, ${ARCANA.holoCyan}55, transparent 70%)`,
        filter: 'blur(20px)',
        animation: animated ? 'aurora-drift 7s ease-in-out 1s infinite reverse' : 'none',
      }} />
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
        <V3AuroraAvatar size={44} eye={eye} animated={animated} />
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 19, fontStyle: 'italic', color: ARCANA.text }}>Кай</span>
            <ArchitectBadge accent={ARCANA.holoMagenta} compact />
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: ARCANA.textDim, marginTop: 3 }}>1 сеанс · 0 ритуалов</div>
        </div>
      </div>
    </div>
  );
}

function V3AuroraDetailCard({ animated, eye }) {
  return (
    <div style={{
      position: 'relative', padding: 20, borderRadius: 20,
      background: 'linear-gradient(160deg, rgba(13,31,26,0.9), rgba(26,13,46,0.9))',
      border: `0.5px solid ${ARCANA.borderStrong}`,
      overflow: 'hidden', minHeight: 280,
    }}>
      {/* aurora blobs */}
      <div style={{
        position: 'absolute', left: '20%', top: -60, width: 200, height: 200, borderRadius: '50%',
        background: `radial-gradient(circle, ${ARCANA.holoMagenta}66, transparent 70%)`,
        filter: 'blur(30px)',
        animation: animated ? 'aurora-drift 8s ease-in-out infinite' : 'none',
      }} />
      <div style={{
        position: 'absolute', right: '10%', top: 40, width: 180, height: 180, borderRadius: '50%',
        background: `radial-gradient(circle, ${ARCANA.holoCyan}55, transparent 70%)`,
        filter: 'blur(30px)',
        animation: animated ? 'aurora-drift 10s ease-in-out 2s infinite reverse' : 'none',
      }} />
      <div style={{
        position: 'absolute', left: '40%', bottom: -50, width: 200, height: 200, borderRadius: '50%',
        background: `radial-gradient(circle, ${ARCANA.holoGold}44, transparent 70%)`,
        filter: 'blur(35px)',
        animation: animated ? 'aurora-drift 9s ease-in-out 1s infinite' : 'none',
      }} />
      <div style={{ position: 'relative', textAlign: 'center' }}>
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 14, marginTop: 4 }}>
          <V3AuroraAvatar size={86} eye={eye} animated={animated} />
        </div>
        <div style={{ fontFamily: FONT_DISPLAY, fontSize: 38, fontStyle: 'italic', color: ARCANA.text, lineHeight: 1 }}>Кай</div>
        <div style={{ marginTop: 6, marginBottom: 14, display: 'flex', justifyContent: 'center' }}>
          <ArchitectBadge accent={ARCANA.holoMagenta} />
        </div>
        <div style={{
          padding: '12px 14px', borderRadius: 12,
          background: 'rgba(0,0,0,0.25)',
          backdropFilter: 'blur(12px)',
          border: `0.5px solid ${ARCANA.border}`,
          textAlign: 'left',
        }}>
          <div style={{ fontFamily: FONT_UI, fontSize: 12, color: ARCANA.textDim, lineHeight: 1.7 }}>
            𝕏 @dontplayad<br/>
            𝕏 @hey_lark<br/>
            <span style={{ fontFamily: FONT_MONO, fontSize: 11 }}>+7 961 931 12 60</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// VARIANT 4 — ARCHITECT SIGIL (dark luxe, engraved frame, drifting dust)
// ═══════════════════════════════════════════════════════════════════════

function DustParticles({ count = 25, animated, color = '#e8c887' }) {
  const rng = (i) => {
    const x = Math.sin(i * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  };
  const parts = React.useMemo(() => Array.from({ length: count }, (_, i) => ({
    x: rng(i + 7) * 100,
    y: rng(i + 17) * 100,
    s: 0.5 + rng(i + 27) * 1.5,
    d: 8 + rng(i + 37) * 8,
    dy: rng(i + 47) * 30,
  })), [count]);
  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'hidden' }}>
      {parts.map((p, i) => (
        <div key={i} style={{
          position: 'absolute',
          left: `${p.x}%`, top: `${p.y}%`,
          width: p.s, height: p.s, borderRadius: '50%',
          background: color, opacity: 0.5,
          boxShadow: `0 0 ${p.s * 3}px ${color}`,
          animation: animated ? `dust-drift ${p.d}s ease-in-out infinite` : 'none',
          ['--dy']: `${-p.dy}px`,
        }} />
      ))}
    </div>
  );
}

function CornerOrnament({ size = 22, color = '#e8c887', position }) {
  const rot = { tl: 0, tr: 90, br: 180, bl: 270 }[position];
  const [t, l] = {
    tl: [6, 6], tr: [6, 'auto'], br: ['auto', 'auto'], bl: ['auto', 6],
  }[position];
  const [r, b] = {
    tl: ['auto', 'auto'], tr: [6, 'auto'], br: [6, 6], bl: ['auto', 6],
  }[position];
  return (
    <svg width={size} height={size} viewBox="0 0 24 24"
         style={{ position: 'absolute', top: t, left: l, right: r, bottom: b, transform: `rotate(${rot}deg)` }}>
      <path d="M2 12 L2 2 L12 2" stroke={color} strokeWidth="0.8" fill="none" />
      <path d="M5 12 L5 5 L12 5" stroke={color} strokeWidth="0.6" fill="none" opacity="0.5" />
      <circle cx="2" cy="2" r="1.2" fill={color} />
    </svg>
  );
}

function V4SigilAvatar({ size = 44, eye = 'rune', animated }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 8,
      background: 'linear-gradient(145deg, #14241f, #0a1410)',
      border: `0.5px solid ${ARCANA.holoGold}66`,
      position: 'relative', overflow: 'hidden',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      boxShadow: `inset 0 0 12px ${ARCANA.holoGold}22, 0 0 8px ${ARCANA.holoGold}22`,
    }}>
      <DustParticles count={8} animated={animated} />
      <EyeSwitch kind={eye} size={size * 0.7} color={ARCANA.holoGold} />
    </div>
  );
}

function V4SigilListCard({ animated, eye }) {
  return (
    <div style={{
      position: 'relative', padding: '14px 16px', borderRadius: 8,
      background: 'linear-gradient(135deg, #0e1a16, #0a1019)',
      border: `0.5px solid ${ARCANA.holoGold}55`,
      boxShadow: `inset 0 0 30px ${ARCANA.holoGold}10, 0 0 0 1px ${ARCANA.holoGold}15, 0 4px 20px rgba(0,0,0,0.5)`,
      overflow: 'hidden',
    }}>
      <CornerOrnament position="tl" color={ARCANA.holoGold} size={14} />
      <CornerOrnament position="tr" color={ARCANA.holoGold} size={14} />
      <CornerOrnament position="bl" color={ARCANA.holoGold} size={14} />
      <CornerOrnament position="br" color={ARCANA.holoGold} size={14} />
      <DustParticles count={10} animated={animated} />
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
        <V4SigilAvatar size={44} eye={eye} animated={animated} />
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 20, color: ARCANA.holoGold, letterSpacing: '0.04em' }}>Кай</span>
            <span style={{ fontFamily: FONT_MONO, fontSize: 8.5, letterSpacing: '0.25em', color: ARCANA.holoGold, opacity: 0.6 }}>· I ·</span>
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: ARCANA.textMute, marginTop: 3, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            архитектор · sigillum
          </div>
        </div>
      </div>
    </div>
  );
}

function V4SigilDetailCard({ animated, eye }) {
  return (
    <div style={{
      position: 'relative', padding: 22, borderRadius: 10,
      background: 'linear-gradient(160deg, #0e1a16, #0a1019)',
      border: `0.5px solid ${ARCANA.holoGold}66`,
      boxShadow: `inset 0 0 40px ${ARCANA.holoGold}10, 0 0 0 1px ${ARCANA.holoGold}20, 0 8px 30px rgba(0,0,0,0.6)`,
      overflow: 'hidden', minHeight: 300,
    }}>
      <CornerOrnament position="tl" color={ARCANA.holoGold} size={22} />
      <CornerOrnament position="tr" color={ARCANA.holoGold} size={22} />
      <CornerOrnament position="bl" color={ARCANA.holoGold} size={22} />
      <CornerOrnament position="br" color={ARCANA.holoGold} size={22} />
      <DustParticles count={30} animated={animated} />
      <div style={{ position: 'relative', textAlign: 'center' }}>
        <div style={{ fontFamily: FONT_MONO, fontSize: 9, color: ARCANA.holoGold, letterSpacing: '0.4em', opacity: 0.7 }}>
          ✦ ARCHITECTUS ✦
        </div>
        <div style={{ marginTop: 16, marginBottom: 10, display: 'flex', justifyContent: 'center' }}>
          <V4SigilAvatar size={92} eye={eye} animated={animated} />
        </div>
        <div style={{ fontFamily: FONT_DISPLAY, fontSize: 40, color: ARCANA.holoGold, letterSpacing: '0.05em', lineHeight: 1, textShadow: `0 0 18px ${ARCANA.holoGold}55` }}>
          Кай
        </div>
        <div style={{ marginTop: 4, fontFamily: FONT_DISPLAY, fontStyle: 'italic', fontSize: 13, color: ARCANA.textDim }}>
          primus · creator
        </div>
        <div style={{
          margin: '18px auto 0', maxWidth: 220,
          height: 1, background: `linear-gradient(90deg, transparent, ${ARCANA.holoGold}, transparent)`,
        }} />
        <div style={{ marginTop: 14, fontFamily: FONT_MONO, fontSize: 10.5, color: ARCANA.textDim, lineHeight: 1.9, letterSpacing: '0.05em' }}>
          @dontplayad &nbsp;·&nbsp; @hey_lark<br/>
          +7 961 931 12 60
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// VARIANT 5 — LIVING EYE (parallax pupil follows pointer)
// ═══════════════════════════════════════════════════════════════════════

function LivingEye({ size = 80, accent = ARCANA.holoCyan, animated }) {
  const ref = React.useRef(null);
  const [pupil, setPupil] = React.useState({ x: 0, y: 0 });
  React.useEffect(() => {
    if (!animated) { setPupil({ x: 0, y: 0 }); return; }
    const handler = (e) => {
      if (!ref.current) return;
      const r = ref.current.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const dx = e.clientX - cx;
      const dy = e.clientY - cy;
      const len = Math.hypot(dx, dy) || 1;
      const max = size * 0.12;
      const k = Math.min(1, len / 200);
      setPupil({ x: (dx / len) * max * k, y: (dy / len) * max * k });
    };
    window.addEventListener('mousemove', handler);
    return () => window.removeEventListener('mousemove', handler);
  }, [size, animated]);

  return (
    <div ref={ref} style={{ width: size, height: size * 0.7, position: 'relative' }}>
      <svg width={size} height={size * 0.7} viewBox="0 0 100 70" style={{ overflow: 'visible' }}>
        <defs>
          <radialGradient id={`iris-${size}`} cx="50%" cy="50%">
            <stop offset="0%" stopColor={accent} stopOpacity="1" />
            <stop offset="60%" stopColor={accent} stopOpacity="0.8" />
            <stop offset="100%" stopColor="#1a0d2e" stopOpacity="1" />
          </radialGradient>
          <radialGradient id={`glow-${size}`} cx="50%" cy="50%">
            <stop offset="0%" stopColor={accent} stopOpacity="0.6" />
            <stop offset="100%" stopColor={accent} stopOpacity="0" />
          </radialGradient>
        </defs>
        {/* outer glow */}
        <ellipse cx="50" cy="35" rx="55" ry="35" fill={`url(#glow-${size})`} opacity={animated ? undefined : 0.4}>
          {animated && <animate attributeName="opacity" values="0.3;0.6;0.3" dur="3s" repeatCount="indefinite" />}
        </ellipse>
        {/* eye outline */}
        <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" fill="#0a0a18" stroke={accent} strokeWidth="0.8" />
        <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" fill="none" stroke="#fff" strokeWidth="0.3" opacity="0.4" />
        {/* iris (follows pointer) */}
        <g transform={`translate(${pupil.x}, ${pupil.y})`} style={{ transition: animated ? 'transform 0.15s ease-out' : 'none' }}>
          <circle cx="50" cy="35" r="14" fill={`url(#iris-${size})`} />
          <circle cx="50" cy="35" r="14" fill="none" stroke={accent} strokeWidth="0.5" opacity="0.6" />
          {/* pupil */}
          <circle cx="50" cy="35" r="6" fill="#000" />
          <circle cx="52" cy="33" r="1.5" fill="#fff" opacity="0.9" />
          {/* iris striations */}
          {Array.from({ length: 12 }, (_, i) => {
            const a = (i / 12) * Math.PI * 2;
            return <line key={i}
              x1={50 + Math.cos(a) * 7} y1={35 + Math.sin(a) * 7}
              x2={50 + Math.cos(a) * 13} y2={35 + Math.sin(a) * 13}
              stroke={accent} strokeWidth="0.4" opacity="0.5" />;
          })}
        </g>
      </svg>
    </div>
  );
}

function V5LivingAvatar({ size = 44, animated }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 10,
      background: 'radial-gradient(circle at 50% 40%, #1a1530, #0a0a18)',
      border: `0.5px solid ${ARCANA.holoCyan}66`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      overflow: 'hidden',
    }}>
      <LivingEye size={size * 0.85} animated={animated} />
    </div>
  );
}

function V5LivingListCard({ animated }) {
  return (
    <div style={{
      position: 'relative', padding: '14px 16px', borderRadius: 14,
      background: 'linear-gradient(135deg, rgba(13,18,32,0.9), rgba(26,13,46,0.9))',
      border: `0.5px solid ${ARCANA.holoCyan}55`,
      boxShadow: `0 0 24px ${ARCANA.holoCyan}15, 0 4px 20px rgba(0,0,0,0.4)`,
      overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', inset: 0,
        background: `radial-gradient(circle at 80% 50%, ${ARCANA.holoViolet}22, transparent 60%)`,
      }} />
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
        <V5LivingAvatar animated={animated} />
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 19, fontStyle: 'italic', color: ARCANA.text }}>Кай</span>
            <ArchitectBadge accent={ARCANA.holoCyan} compact />
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: ARCANA.textDim, marginTop: 3 }}>
            <span style={{ color: ARCANA.holoCyan, opacity: 0.8 }}>◉</span> наблюдает · 1 сеанс
          </div>
        </div>
      </div>
    </div>
  );
}

function V5LivingDetailCard({ animated }) {
  return (
    <div style={{
      position: 'relative', padding: 20, borderRadius: 18,
      background: 'linear-gradient(160deg, rgba(13,18,32,0.95), rgba(26,13,46,0.95))',
      border: `0.5px solid ${ARCANA.holoCyan}55`,
      boxShadow: `0 0 40px ${ARCANA.holoCyan}20, 0 8px 30px rgba(0,0,0,0.5)`,
      overflow: 'hidden', minHeight: 300,
    }}>
      <div style={{
        position: 'absolute', inset: 0,
        background: `radial-gradient(circle at 50% 35%, ${ARCANA.holoViolet}33, transparent 55%)`,
      }} />
      <StarField count={25} seed={9} opacity={0.5} animated={animated} />
      <div style={{ position: 'relative', textAlign: 'center' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <span style={{ fontFamily: FONT_MONO, fontSize: 9, color: ARCANA.holoCyan, letterSpacing: '0.25em' }}>VIDET · OMNIA</span>
          <ArchitectBadge accent={ARCANA.holoCyan} compact />
        </div>
        <div style={{ display: 'flex', justifyContent: 'center', padding: '12px 0' }}>
          <LivingEye size={150} animated={animated} accent={ARCANA.holoCyan} />
        </div>
        <div style={{ fontFamily: FONT_DISPLAY, fontSize: 36, fontStyle: 'italic', color: ARCANA.text, lineHeight: 1, marginTop: 4 }}>Кай</div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: ARCANA.holoCyan, marginTop: 6, letterSpacing: '0.3em', textTransform: 'uppercase' }}>
          всевидящий · наблюдатель
        </div>
        <div style={{
          marginTop: 16, padding: '12px 14px', borderRadius: 10,
          background: 'rgba(255,255,255,0.03)', border: `0.5px solid ${ARCANA.border}`,
          textAlign: 'left',
        }}>
          <div style={{ fontFamily: FONT_UI, fontSize: 12, color: ARCANA.textDim, lineHeight: 1.7 }}>
            𝕏 @dontplayad · @hey_lark<br/>
            <span style={{ fontFamily: FONT_MONO, fontSize: 11 }}>+7 961 931 12 60</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// VARIANT 6 — THE ONE (combined: V1 holo frame + V3 aurora ring + V5 living eye)
// ═══════════════════════════════════════════════════════════════════════

// Living eye CLAMPED — pupil stays inside the eye outline (clipped by lid).
function ClampedLivingEye({ size = 80, accent = ARCANA.holoCyan, animated, bgFill = '#0a0a18' }) {
  const ref = React.useRef(null);
  const [pupil, setPupil] = React.useState({ x: 0, y: 0 });

  React.useEffect(() => {
    if (!animated) { setPupil({ x: 0, y: 0 }); return; }
    const handler = (e) => {
      if (!ref.current) return;
      const r = ref.current.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const dx = e.clientX - cx;
      const dy = e.clientY - cy;
      const len = Math.hypot(dx, dy) || 1;
      // Iris travels freely up to the lid edge — it can disappear under the lid.
      // viewBox is 100×70 with eye outline rx≈45 ry≈33; iris r=14.
      // Allow large travel: max ≈ 22 horizontally, 14 vertically; clipPath handles cutoff.
      const k = Math.min(1, len / 180);
      const maxX = 22;
      const maxY = 14;
      setPupil({ x: (dx / len) * maxX * k, y: (dy / len) * maxY * k });
    };
    window.addEventListener('mousemove', handler);
    return () => window.removeEventListener('mousemove', handler);
  }, [animated]);

  const uid = `eye-${size}-${accent.replace('#','')}`;

  return (
    <div ref={ref} style={{ width: size, height: size * 0.7, position: 'relative' }}>
      <svg width={size} height={size * 0.7} viewBox="0 0 100 70" style={{ overflow: 'visible' }}>
        <defs>
          <radialGradient id={`iris-${uid}`} cx="50%" cy="50%">
            <stop offset="0%"  stopColor={accent} stopOpacity="1" />
            <stop offset="55%" stopColor={accent} stopOpacity="0.85" />
            <stop offset="100%" stopColor="#1a0d2e" stopOpacity="1" />
          </radialGradient>
          <radialGradient id={`glow-${uid}`} cx="50%" cy="50%">
            <stop offset="0%" stopColor={accent} stopOpacity="0.55" />
            <stop offset="100%" stopColor={accent} stopOpacity="0" />
          </radialGradient>
          {/* Lid clip — exact eye-outline shape */}
          <clipPath id={`clip-${uid}`}>
            <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" />
          </clipPath>
        </defs>
        {bgFill !== 'transparent' && <ellipse cx="50" cy="35" rx="55" ry="35" fill={`url(#glow-${uid})`}>
          {animated && <animate attributeName="opacity" values="0.5;0.9;0.5" dur="3.5s" repeatCount="indefinite" />}
        </ellipse>}
        {/* Sclera background — drawn in eye shape */}
        {bgFill !== 'transparent' && <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" fill={bgFill} />}
        {/* Iris+pupil — clipped by lid, so they vanish under the eyelid edges */}
        <g clipPath={`url(#clip-${uid})`}>
          <g transform={`translate(${pupil.x}, ${pupil.y})`} style={{ transition: animated ? 'transform 0.18s ease-out' : 'none' }}>
            <circle cx="50" cy="35" r="18" fill={`url(#iris-${uid})`} />
            <circle cx="50" cy="35" r="18" fill="none" stroke={accent} strokeWidth="0.6" opacity="0.7" />
            {Array.from({ length: 14 }, (_, i) => {
              const a = (i / 14) * Math.PI * 2;
              return <line key={i}
                x1={50 + Math.cos(a) * 9} y1={35 + Math.sin(a) * 9}
                x2={50 + Math.cos(a) * 17} y2={35 + Math.sin(a) * 17}
                stroke={accent} strokeWidth="0.4" opacity="0.55" />;
            })}
            <circle cx="50" cy="35" r="9" fill="#000" />
            <circle cx="53" cy="32.5" r="2.2" fill="#fff" opacity="0.9" />
          </g>
        </g>
        {/* Eye outline — drawn AFTER iris so the lid edge sits on top */}
        <path d="M5 35 Q50 -2 95 35 Q50 72 5 35 Z" fill="none" stroke={accent} strokeWidth="0.9" />
        {/* upper-lid highlight */}
        <path d="M8 33 Q50 0 92 33" fill="none" stroke="#fff" strokeWidth="0.4" opacity="0.35" />
      </svg>
    </div>
  );
}

// List-row avatar — bare living eye, no frame at all (sits naturally in the 44px slot).
function V6OneAvatar({ size = 44, animated, accent = ARCANA.holoCyan }) {
  return (
    <div style={{
      width: size, height: size,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      position: 'relative',
    }}>
      <ClampedLivingEye size={size * 1.5} animated={animated} accent={accent} bgFill="transparent" />
    </div>
  );
}

// Detail-screen showpiece avatar — keeps the V3 aurora ring (only used at large size).
function V6OneAvatarBig({ size = 110, animated, accent = ARCANA.holoCyan }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      position: 'relative',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        position: 'absolute', inset: -8, borderRadius: '50%',
        background: `radial-gradient(circle, ${accent}88, ${ARCANA.holoMagenta}55, transparent 70%)`,
        filter: 'blur(10px)',
        animation: animated ? 'aurora-breathe 3.5s ease-in-out infinite' : 'none',
      }} />
      <div style={{
        position: 'absolute', inset: 0, borderRadius: '50%',
        background: `conic-gradient(from 90deg, ${ARCANA.holoCyan}, ${ARCANA.holoMagenta}, ${ARCANA.holoGold}, ${ARCANA.holoViolet}, ${ARCANA.holoCyan})`,
        animation: animated ? 'holo-spin 8s linear infinite' : 'none',
        opacity: 0.95,
      }} />
      <div style={{
        position: 'absolute', inset: 3, borderRadius: '50%',
        background: 'radial-gradient(circle at 50% 40%, #1a1530, #0a0a18)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        overflow: 'hidden',
      }}>
        <ClampedLivingEye size={size * 0.78} animated={animated} accent={accent} bgFill="#0a0a18" />
      </div>
    </div>
  );
}

// Holo foil card frame (from V1) hosting our combined avatar
function V6OneListCard({ animated }) {
  return (
    <div style={{
      position: 'relative',
      padding: '14px 16px',
      borderRadius: 14,
      background: 'linear-gradient(135deg, rgba(13,31,26,0.92), rgba(26,13,46,0.92))',
      overflow: 'hidden',
    }}>
      {/* holographic running border */}
      <div style={{
        position: 'absolute', inset: 0, borderRadius: 14, padding: 1,
        background: `linear-gradient(120deg,
          ${ARCANA.holoCyan}, ${ARCANA.holoMagenta}, ${ARCANA.holoGold}, ${ARCANA.holoViolet}, ${ARCANA.holoCyan})`,
        backgroundSize: '300% 100%',
        WebkitMask: 'linear-gradient(#fff,#fff) content-box, linear-gradient(#fff,#fff)',
        WebkitMaskComposite: 'xor', maskComposite: 'exclude',
        animation: animated ? 'holo-border 4s linear infinite' : 'none',
        opacity: 0.9,
      }} />
      {animated && <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(115deg, transparent 35%, rgba(255,255,255,0.08) 50%, transparent 65%)',
        animation: 'holo-shine 5s ease-in-out infinite',
        pointerEvents: 'none',
      }} />}
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
        <V6OneAvatar size={44} animated={animated} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 19, fontStyle: 'italic', color: ARCANA.text }}>Кай</span>
            <ArchitectBadge accent={ARCANA.holoGold} compact />
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: ARCANA.textDim, marginTop: 3, letterSpacing: '0.05em' }}>
            1 сеанс · 0 ритуалов
          </div>
        </div>
      </div>
    </div>
  );
}

// Card-interior backgrounds (selectable via Tweaks)
function CardBackground({ kind = 'glow', animated }) {
  if (kind === 'none') return null;

  if (kind === 'glow') {
    return (
      <div style={{
        position: 'absolute', left: '50%', top: '38%',
        width: 240, height: 240, borderRadius: '50%',
        transform: 'translate(-50%, -50%)',
        background: `radial-gradient(circle, ${ARCANA.holoCyan}33, ${ARCANA.holoMagenta}22 40%, transparent 70%)`,
        filter: 'blur(20px)',
      }} />
    );
  }

  if (kind === 'dust') {
    // holographic dust drifting upward
    return (
      <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
        <DustParticles count={28} animated={animated} color={ARCANA.holoCyan} />
        <DustParticles count={18} animated={animated} color={ARCANA.holoMagenta} />
        <DustParticles count={12} animated={animated} color={ARCANA.holoGold} />
      </div>
    );
  }

  if (kind === 'stars') {
    return (
      <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
        <StarField count={70} seed={71} opacity={0.8} animated={animated} />
      </div>
    );
  }

  if (kind === 'sigil') {
    // grid only — geometric shapes are drawn around the eye instead (handled in detail card)
    return (
      <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: `linear-gradient(${ARCANA.holoCyan}10 1px, transparent 1px),
                            linear-gradient(90deg, ${ARCANA.holoCyan}10 1px, transparent 1px)`,
          backgroundSize: '24px 24px',
          maskImage: 'radial-gradient(circle at 50% 32%, black 30%, transparent 75%)',
          WebkitMaskImage: 'radial-gradient(circle at 50% 32%, black 30%, transparent 75%)',
        }} />
      </div>
    );
  }

  if (kind === 'mist') {
    // soft drifting blobs
    return (
      <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
        <div style={{
          position: 'absolute', left: '15%', top: '20%', width: 180, height: 180, borderRadius: '50%',
          background: `radial-gradient(circle, ${ARCANA.holoCyan}33, transparent 70%)`,
          filter: 'blur(30px)',
          animation: animated ? 'aurora-drift 9s ease-in-out infinite' : 'none',
        }} />
        <div style={{
          position: 'absolute', right: '10%', top: '50%', width: 200, height: 200, borderRadius: '50%',
          background: `radial-gradient(circle, ${ARCANA.holoMagenta}33, transparent 70%)`,
          filter: 'blur(35px)',
          animation: animated ? 'aurora-drift 11s ease-in-out 2s infinite reverse' : 'none',
        }} />
        <div style={{
          position: 'absolute', left: '40%', bottom: '5%', width: 180, height: 180, borderRadius: '50%',
          background: `radial-gradient(circle, ${ARCANA.holoGold}28, transparent 70%)`,
          filter: 'blur(35px)',
          animation: animated ? 'aurora-drift 12s ease-in-out 1s infinite' : 'none',
        }} />
      </div>
    );
  }

  if (kind === 'tarot') {
    // faint Tarot card "Звезда" silhouette in the background
    return (
      <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
        <StarField count={30} seed={3} opacity={0.45} animated={animated} />
        <svg style={{ position: 'absolute', left: '50%', top: '8%', transform: 'translateX(-50%)', opacity: 0.13 }}
             width="180" height="280" viewBox="0 0 180 280">
          <rect x="6" y="6" width="168" height="268" rx="12" fill="none" stroke={ARCANA.holoGold} strokeWidth="1" />
          <rect x="14" y="14" width="152" height="252" rx="8" fill="none" stroke={ARCANA.holoGold} strokeWidth="0.5" opacity="0.6" />
          {/* big 8-pointed star */}
          <g transform="translate(90 130)" stroke={ARCANA.holoGold} strokeWidth="1.2" fill="none">
            {[0,1,2,3,4,5,6,7].map(i => {
              const a = (i / 8) * Math.PI * 2;
              const r = i % 2 === 0 ? 50 : 22;
              return <line key={i} x1="0" y1="0" x2={Math.cos(a) * r} y2={Math.sin(a) * r} />;
            })}
            <circle r="6" fill={ARCANA.holoGold} fillOpacity="0.4" />
          </g>
          {/* 7 small stars around */}
          {[[35,60],[145,60],[35,200],[145,200],[60,30],[120,30],[90,240]].map(([x,y], i) => (
            <g key={i} transform={`translate(${x} ${y})`} stroke={ARCANA.holoGold} strokeWidth="0.6" fill="none">
              {[0,1,2,3].map(j => {
                const a = (j / 4) * Math.PI * 2 + Math.PI / 4;
                return <line key={j} x1="0" y1="0" x2={Math.cos(a) * 7} y2={Math.sin(a) * 7} />;
              })}
            </g>
          ))}
          <text x="90" y="262" textAnchor="middle" fontFamily={FONT_DISPLAY} fontSize="11" fill={ARCANA.holoGold} fontStyle="italic">THE STAR</text>
        </svg>
      </div>
    );
  }

  return null;
}

// Detail card: V1 holo frame + giant clamped living eye + architect tagline (from V5)
function V6OneDetailCard({ animated, bg = 'glow' }) {
  return (
    <div style={{
      position: 'relative', padding: 20, borderRadius: 18,
      background: 'linear-gradient(160deg, rgba(13,31,26,0.95), rgba(26,13,46,0.95))',
      overflow: 'hidden', minHeight: 320,
    }}>
      {/* holographic outer rim */}
      <div style={{
        position: 'absolute', inset: 0, borderRadius: 18, padding: 1.2,
        background: `linear-gradient(120deg,
          ${ARCANA.holoCyan}, ${ARCANA.holoMagenta}, ${ARCANA.holoGold}, ${ARCANA.holoViolet}, ${ARCANA.holoCyan})`,
        backgroundSize: '300% 100%',
        WebkitMask: 'linear-gradient(#fff,#fff) content-box, linear-gradient(#fff,#fff)',
        WebkitMaskComposite: 'xor', maskComposite: 'exclude',
        animation: animated ? 'holo-border 5s linear infinite' : 'none',
        zIndex: 3,
      }} />
      {animated && <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.07) 50%, transparent 70%)',
        animation: 'holo-shine 6s ease-in-out infinite',
        zIndex: 2,
      }} />}

      {/* selectable background fill */}
      <CardBackground kind={bg} animated={animated} />
      <div style={{ position: 'relative', textAlign: 'center' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontFamily: FONT_MONO, fontSize: 9, color: ARCANA.holoCyan, letterSpacing: '0.25em' }}>FOIL · 001/001</span>
          <ArchitectBadge accent={ARCANA.holoGold} compact />
        </div>

        {/* Giant living eye with optional concentric sigil wrapped around it */}
        <div style={{ display: 'flex', justifyContent: 'center', padding: '14px 0 6px', position: 'relative' }}>
          {bg === 'sigil' && (
            <svg style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)', width: 210, height: 210, pointerEvents: 'none' }} viewBox="0 0 320 320">
              <g stroke={`${ARCANA.holoGold}55`} fill="none" strokeWidth="0.8">
                <circle cx="160" cy="160" r="150" />
                <circle cx="160" cy="160" r="120" strokeDasharray="3 5" />
                <polygon points="160,30 272,224 48,224" />
                <polygon points="160,290 48,96 272,96" opacity="0.5" />
              </g>
            </svg>
          )}
          <ClampedLivingEye size={150} animated={animated} accent={ARCANA.holoCyan} bgFill="#0a0e18" />
        </div>

        <div style={{ fontFamily: FONT_DISPLAY, fontSize: 38, fontStyle: 'italic', color: ARCANA.text, lineHeight: 1, marginTop: 6 }}>Кай</div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: ARCANA.holoCyan, marginTop: 7, letterSpacing: '0.32em', textTransform: 'uppercase', opacity: 0.9 }}>
          архитектор · видящий
        </div>

        <div style={{
          margin: '16px auto 0', maxWidth: 240,
          height: 1, background: `linear-gradient(90deg, transparent, ${ARCANA.holoMagenta}66, transparent)`,
        }} />

        <div style={{
          marginTop: 14, padding: '12px 14px', borderRadius: 12,
          background: 'rgba(0,0,0,0.28)', border: `0.5px solid ${ARCANA.border}`,
          textAlign: 'left',
        }}>
          <div style={{ fontFamily: FONT_UI, fontSize: 12, color: ARCANA.textDim, lineHeight: 1.7 }}>
            𝕏 @dontplayad · @hey_lark<br/>
            <span style={{ fontFamily: FONT_MONO, fontSize: 11 }}>+7 961 931 12 60</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Variant registry ────────────────────────────────────────────────────

const VARIANTS = {
  one:    { label: 'THE ONE',       listEl: V6OneListCard,    detailEl: V6OneDetailCard,   defaultEye: 'open' },
  holo:   { label: 'Holo Foil',     listEl: V1HoloListCard,   detailEl: V1HoloDetailCard,  defaultEye: 'open' },
  const:  { label: 'Constellation', listEl: V2ConstListCard,  detailEl: V2ConstDetailCard, defaultEye: 'open' },
  aurora: { label: 'Aurora Glow',   listEl: V3AuroraListCard, detailEl: V3AuroraDetailCard,defaultEye: 'open' },
  sigil:  { label: 'Architect Sigil',listEl: V4SigilListCard, detailEl: V4SigilDetailCard, defaultEye: 'rune' },
  living: { label: 'Living Eye',    listEl: V5LivingListCard, detailEl: V5LivingDetailCard,defaultEye: 'open' },
};

Object.assign(window, { VARIANTS });
