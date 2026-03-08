import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Bolt, ChevronDown } from "lucide-react";
import type { DepthLevel } from "../../stores/useChatStore";

interface DepthOption {
  id: DepthLevel;
  label: string;
  subtitle: string;
}

const DEPTH_OPTIONS: DepthOption[] = [
  { id: "eli5", label: "ELI 5", subtitle: "Very simple" },
  { id: "eli10", label: "ELI 10", subtitle: "Kid-friendly" },
  { id: "eli12", label: "ELI 12", subtitle: "Default depth" },
  { id: "eli15", label: "ELI 15", subtitle: "High-school detail" },
  { id: "meme", label: "Meme Mode", subtitle: "Humor + analogies" },
];

interface DepthDropdownProps {
  value: DepthLevel;
  onChange: (level: DepthLevel) => void;
}

export default function DepthDropdown({
  value,
  onChange,
}: DepthDropdownProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const pendingFocusIndex = useRef<number | null>(null);
  const listboxId = useId();

  const selectedOption = useMemo(
    () =>
      DEPTH_OPTIONS.find((option) => option.id === value) ?? DEPTH_OPTIONS[2],
    [value],
  );

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!dropdownRef.current) return;
      if (dropdownRef.current.contains(event.target as Node)) return;
      setOpen(false);
    };

    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  useEffect(() => {
    if (!open) return;
    const selectedIndex = DEPTH_OPTIONS.findIndex(
      (option) => option.id === value,
    );
    const nextIndex =
      pendingFocusIndex.current ?? (selectedIndex >= 0 ? selectedIndex : 0);
    pendingFocusIndex.current = null;
    setActiveIndex(nextIndex);
    requestAnimationFrame(() => optionRefs.current[nextIndex]?.focus());
  }, [open, value]);

  const closeDropdown = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) {
      requestAnimationFrame(() => triggerRef.current?.focus());
    }
  };

  const selectOption = (option: DepthOption, returnFocus: boolean) => {
    onChange(option.id);
    closeDropdown(returnFocus);
  };

  const focusIndex = (index: number) => {
    setActiveIndex(index);
    optionRefs.current[index]?.focus();
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        ref={triggerRef}
        type="button"
        aria-label="Choose explanation depth"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            pendingFocusIndex.current =
              event.key === "ArrowUp" ? DEPTH_OPTIONS.length - 1 : 0;
            setOpen(true);
          }
        }}
        className="inline-flex h-10 items-center gap-2 rounded-xl border border-slate-300 bg-teal-50 px-3 text-sm font-semibold text-slate-700 transition hover:bg-teal-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50 dark:border-white/10 dark:bg-dark-700 dark:text-slate-200 dark:hover:bg-dark-600"
      >
        <Bolt className="h-3.5 w-3.5 text-teal-600 dark:text-cyan-300" />
        {selectedOption.label}
        <ChevronDown
          className={`h-3.5 w-3.5 transition ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label="Depth levels"
          className="absolute bottom-full left-0 mb-2 w-52 rounded-2xl border border-slate-300 bg-white p-2 shadow-xl dark:border-white/10 dark:bg-dark-800"
        >
          {DEPTH_OPTIONS.map((option, index) => {
            const isSelected = option.id === value;
            return (
              <button
                key={option.id}
                role="option"
                aria-selected={isSelected}
                tabIndex={activeIndex === index ? 0 : -1}
                ref={(element) => {
                  optionRefs.current[index] = element;
                }}
                onFocus={() => setActiveIndex(index)}
                onClick={(event) => {
                  const returnFocus = event.detail === 0;
                  selectOption(option, returnFocus);
                }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    focusIndex((index + 1) % DEPTH_OPTIONS.length);
                    return;
                  }
                  if (event.key === "ArrowUp") {
                    event.preventDefault();
                    focusIndex(
                      (index - 1 + DEPTH_OPTIONS.length) % DEPTH_OPTIONS.length,
                    );
                    return;
                  }
                  if (event.key === "Home") {
                    event.preventDefault();
                    focusIndex(0);
                    return;
                  }
                  if (event.key === "End") {
                    event.preventDefault();
                    focusIndex(DEPTH_OPTIONS.length - 1);
                    return;
                  }
                  if (event.key === "Escape") {
                    event.preventDefault();
                    closeDropdown(true);
                    return;
                  }
                  if (event.key === "Tab") {
                    closeDropdown(false);
                    return;
                  }
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    selectOption(option, true);
                  }
                }}
                className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left transition ${
                  isSelected
                    ? "bg-teal-50 text-slate-900 dark:bg-teal-500/10 dark:text-white"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-white/5"
                }`}
              >
                <span className="text-sm font-semibold">{option.label}</span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {option.subtitle}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
