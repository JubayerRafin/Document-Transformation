export function Brand({ studio = false }: { studio?: boolean }) {
  return <a href="/" aria-label="TransformX home" className="inline-flex shrink-0 items-center gap-2 text-xl font-semibold tracking-[-.05em]">
    <img src="/transformx-mark.svg" width={40} height={40} alt="" aria-hidden="true" className="size-10" />
    <span>Transform<span className="text-primary">X</span>{studio&&<span className="ml-2 text-sm font-normal tracking-normal text-muted-foreground">Studio</span>}</span>
  </a>;
}
