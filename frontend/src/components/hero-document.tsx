"use client";
import { useEffect, useRef, useState } from "react";
import type { DocumentSceneController } from "./document-scene";

// Always render a useful illustration; WebGL enhances it only on larger screens.
export function HeroDocument({ paused }: { paused: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<DocumentSceneController | null>(null);
  const pausedRef = useRef(paused);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    pausedRef.current = paused;
    controller.current?.setPaused(paused);
  }, [paused]);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const media = window.matchMedia("(min-width: 900px) and (prefers-reduced-motion: no-preference)");
    let disposed = false, generation = 0;
    const update = () => {
      const current = ++generation;
      controller.current?.dispose();
      controller.current = null;
      setReady(false);
      if (!media.matches) return;
      void import("./document-scene").then(({ createDocumentScene }) => {
        if (disposed || current !== generation) return;
        const scene = createDocumentScene(element, () => {
          if (!disposed && current === generation) setReady(false);
        });
        if (!scene) return;
        controller.current = scene;
        scene.setPaused(pausedRef.current);
        setReady(true);
      }).catch(() => { /* The SVG stays visible if the enhancement cannot load. */ });
    };
    update();
    media.addEventListener("change", update);
    return () => {
      disposed = true;
      generation++;
      media.removeEventListener("change", update);
      controller.current?.dispose();
      controller.current = null;
    };
  }, []);
  return <div className="tx-hero-document" aria-hidden="true" data-renderer={ready ? "webgl" : "static"}>
    <div className="tx-document-orbit" />
    <svg className="tx-document-fallback" viewBox="0 0 520 320" fill="none">
      <ellipse cx="268" cy="278" rx="150" ry="18" fill="#addeff" opacity=".08" />
      <g transform="translate(133 41) rotate(-16 100 110)">
        <rect width="168" height="222" rx="10" fill="#465d6d" stroke="#83a6bd" />
        <path d="M25 38h80M25 53h116M25 69h100" stroke="#99b4c6" strokeWidth="5" />
      </g>
      <g transform="translate(190 30) rotate(12 88 110)">
        <rect width="168" height="222" rx="10" fill="#a6d1ed" stroke="#ceebff" />
        <path d="M24 34h62M24 61h113M24 77h94M24 93h106" stroke="#55778e" strokeWidth="5" />
        <rect x="23" y="116" width="122" height="51" rx="4" fill="#89b1cc" />
      </g>
      <g transform="translate(268 91) rotate(18)">
        <rect width="126" height="160" rx="9" fill="#eaf1f6" stroke="#fff" />
        <path d="M19 27h62M19 43h86" stroke="#859eaf" strokeWidth="5" />
        <rect x="18" y="67" width="78" height="12" rx="3" fill="#182833" />
        <rect x="18" y="89" width="60" height="12" rx="3" fill="#182833" />
        <path d="m87 113 13 5v10c0 9-8 15-13 17-5-2-13-8-13-17v-10z" fill="#9beeff" stroke="#4d909e" />
      </g>
    </svg>
    <div className="tx-document-canvas" ref={host} />
    <span className="tx-document-chip tx-document-chip-language">EN <span>→</span> KO</span>
    <span className="tx-document-chip tx-document-chip-private">✓ PRIVATE BY DESIGN</span>
    <span className="tx-document-caption">TRANSLATE / PROTECT / PRESERVE · ILLUSTRATION</span>
  </div>;
}
