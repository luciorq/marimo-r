/* Copyright 2026 Marimo. All rights reserved. */

import { SettingsIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip } from "@/components/ui/tooltip";
import {
  DEFAULT_R_PLOT_DPI,
  DEFAULT_R_PLOT_FORMAT,
  DEFAULT_R_PLOT_HEIGHT,
  DEFAULT_R_PLOT_WIDTH,
  type RLanguageMetadata,
} from "../languages/r";

interface RPlotSettingsProps {
  metadata: RLanguageMetadata;
  onUpdate: (update: Partial<RLanguageMetadata>) => void;
}

export const RPlotSettings: React.FC<RPlotSettingsProps> = ({
  metadata,
  onUpdate,
}) => {
  const plotFormat = metadata.plotFormat ?? DEFAULT_R_PLOT_FORMAT;
  const plotWidth = metadata.plotWidth ?? DEFAULT_R_PLOT_WIDTH;
  const plotHeight = metadata.plotHeight ?? DEFAULT_R_PLOT_HEIGHT;
  const plotDpi = metadata.plotDpi ?? DEFAULT_R_PLOT_DPI;

  const hasNonDefaultSettings =
    plotFormat !== DEFAULT_R_PLOT_FORMAT ||
    plotWidth !== DEFAULT_R_PLOT_WIDTH ||
    plotHeight !== DEFAULT_R_PLOT_HEIGHT ||
    plotDpi !== DEFAULT_R_PLOT_DPI;

  return (
    <Popover>
      <Tooltip content="Plot settings">
        <PopoverTrigger asChild={true}>
          <Button
            variant="text"
            size="icon"
            className={hasNonDefaultSettings ? "text-accent-foreground" : ""}
          >
            <SettingsIcon className="h-3 w-3" />
          </Button>
        </PopoverTrigger>
      </Tooltip>
      <PopoverContent className="w-56 p-3" align="end">
        <div className="flex flex-col gap-3">
          <h4 className="text-xs font-semibold">Plot Settings</h4>

          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">Format</span>
            <Select
              value={plotFormat}
              onValueChange={(value) => onUpdate({ plotFormat: value })}
            >
              <SelectTrigger className="h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="png">PNG</SelectItem>
                <SelectItem value="svg">SVG</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">
              Width (pixels)
            </span>
            <input
              type="number"
              min={100}
              max={4000}
              step={10}
              value={plotWidth}
              onChange={(e) => {
                const val = Number.parseInt(e.target.value, 10);
                if (!Number.isNaN(val) && val > 0) {
                  onUpdate({ plotWidth: val });
                }
              }}
              className="h-7 w-full border border-border rounded px-2 text-xs focus-visible:outline-hidden focus-visible:ring-1 focus-visible:ring-ring"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">
              Height (pixels)
            </span>
            <input
              type="number"
              min={100}
              max={4000}
              step={10}
              value={plotHeight}
              onChange={(e) => {
                const val = Number.parseInt(e.target.value, 10);
                if (!Number.isNaN(val) && val > 0) {
                  onUpdate({ plotHeight: val });
                }
              }}
              className="h-7 w-full border border-border rounded px-2 text-xs focus-visible:outline-hidden focus-visible:ring-1 focus-visible:ring-ring"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">DPI</span>
            <input
              type="number"
              min={36}
              max={600}
              step={1}
              value={plotDpi}
              onChange={(e) => {
                const val = Number.parseInt(e.target.value, 10);
                if (!Number.isNaN(val) && val > 0) {
                  onUpdate({ plotDpi: val });
                }
              }}
              className="h-7 w-full border border-border rounded px-2 text-xs focus-visible:outline-hidden focus-visible:ring-1 focus-visible:ring-ring"
            />
          </label>

          {hasNonDefaultSettings && (
            <Button
              variant="outline"
              size="sm"
              className="text-xs h-7"
              onClick={() =>
                onUpdate({
                  plotFormat: DEFAULT_R_PLOT_FORMAT,
                  plotWidth: DEFAULT_R_PLOT_WIDTH,
                  plotHeight: DEFAULT_R_PLOT_HEIGHT,
                  plotDpi: DEFAULT_R_PLOT_DPI,
                })
              }
            >
              Reset to defaults
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
};
