"use client";
// The UI kit showcase: MarketLens components (AppComponents.jsx) first, then colours, fonts, number formats and
// all 30 shadcn/ui components with sample data.
// Switch the theme with the sun / moon button to check both looks. Made-up data only.
import { ChevronsUpDown, Info, Power, Search, TrendingDown, TrendingUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import ThemeToggle from "@/components/ThemeToggle";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from "@/components/ui/command";
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import AppComponents from "./AppComponents";
import {
  formatDate, formatInt, formatPct, formatRelative, formatRs, formatTimeIST, pnlClass,
} from "@/lib/format";

// Made-up trades for the table.
const TRADES = [
  { date: "2026-11-16", symbol: "NIFTY 25000 CE", side: "SELL", qty: 65, pnl: 4875.5 },
  { date: "2026-11-17", symbol: "BANKNIFTY 52000 PE", side: "BUY", qty: 30, pnl: -2310 },
  { date: "2026-11-18", symbol: "RELIANCE", side: "BUY", qty: 100, pnl: 12345678.5 },
  { date: "2026-11-19", symbol: "NIFTYBEES", side: "SELL", qty: 400, pnl: 0 },
];
// Every theme colour, to see them side by side.
const SWATCHES = [
  ["background", "bg-background"], ["card", "bg-card"], ["muted", "bg-muted"], ["primary", "bg-primary"],
  ["profit", "bg-profit"], ["loss", "bg-loss"], ["warning", "bg-warning"], ["live", "bg-live"],
  ["paper", "bg-paper"], ["demo", "bg-demo"], ["chart-1", "bg-chart-1"], ["chart-2", "bg-chart-2"],
  ["chart-3", "bg-chart-3"], ["chart-4", "bg-chart-4"], ["chart-5", "bg-chart-5"],
];
// A fixed "now" for the sample times (the page draws the same thing every time).
const SAMPLE_NOW = Date.parse("2026-11-16T12:00:00+05:30");
// The made-up symbols for the command search box.
const SYMBOLS = ["NIFTY 50", "BANK NIFTY", "RELIANCE", "TCS", "HDFCBANK", "NIFTYBEES", "GOLDBEES"];

// One titled block of the showcase.
function Section({ title, children }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

// Colour swatches with their names.
function Swatches() {
  return (
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
      {SWATCHES.map(([name, cls]) => (
        <div key={name} className="space-y-1">
          <div className={`h-12 rounded-lg border ${cls}`} />
          <p className="text-xs text-muted-foreground">{name}</p>
        </div>
      ))}
    </div>
  );
}

// Text on the trading colours: plain, on a 10 % tint and on a solid fill.
function TradingColours() {
  const names = ["profit", "loss", "warning", "live", "paper", "demo"];
  // class names written out in full, so Tailwind finds them
  const text = { profit: "text-profit", loss: "text-loss", warning: "text-warning", live: "text-live",
    paper: "text-paper", demo: "text-demo" };
  const tint = { profit: "bg-profit/10 text-profit", loss: "bg-loss/10 text-loss", warning: "bg-warning/10 text-warning",
    live: "bg-live/10 text-live", paper: "bg-paper/10 text-paper", demo: "bg-demo/10 text-demo" };
  const solid = { profit: "bg-profit text-profit-foreground", loss: "bg-loss text-loss-foreground",
    warning: "bg-warning text-warning-foreground", live: "bg-live text-live-foreground",
    paper: "bg-paper text-paper-foreground", demo: "bg-demo text-demo-foreground" };
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {names.map((name) => (
        <div key={name} className="flex items-center gap-2 text-sm">
          <span className={`w-16 ${text[name]}`}>{name}</span>
          <span className={`rounded px-2 py-0.5 ${tint[name]}`}>tint</span>
          <span className={`rounded px-2 py-0.5 font-semibold ${solid[name]}`}>solid</span>
        </div>
      ))}
    </div>
  );
}

// Fonts and the number formats from lib/format.js.
function Typography() {
  const now = SAMPLE_NOW;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-1">
        <p className="text-2xl font-semibold">Inter for text</p>
        <p className="text-muted-foreground">The quick brown fox jumps over the lazy dog.</p>
        <p className="font-mono">JetBrains Mono for code: python -m marketlens.api</p>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">formatRs</dt><dd className="num">{formatRs(12345678.5)}</dd>
        <dt className="text-muted-foreground">formatRs signed</dt><dd className="num text-profit">{formatRs(4875.5, 2, true)}</dd>
        <dt className="text-muted-foreground">formatPct signed</dt><dd className="num text-loss">{formatPct(-1.234, 2, true)}</dd>
        <dt className="text-muted-foreground">formatInt</dt><dd className="num">{formatInt(42318217)}</dd>
        <dt className="text-muted-foreground">formatDate</dt><dd className="num">{formatDate("2026-11-16")}</dd>
        <dt className="text-muted-foreground">formatTimeIST</dt><dd className="num">{formatTimeIST("2026-11-16T09:15:00+05:30")}</dd>
        <dt className="text-muted-foreground">formatRelative</dt>
        <dd className="num">{formatRelative(now - 5 * 60 * 1000, now)}</dd>
      </dl>
    </div>
  );
}

// Buttons, badges, tooltip, dropdown and toasts.
function Buttons() {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button>Run backtest</Button>
        <Button variant="secondary">Save</Button>
        <Button variant="outline">Cancel</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="destructive"><Power /> Kill switch</Button>
        <Button variant="link">Read more</Button>
        <Button size="sm">Small</Button>
        <Button size="lg">Large</Button>
        <Button disabled>Disabled</Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge>Default</Badge>
        <Badge variant="secondary">Secondary</Badge>
        <Badge variant="outline">Outline</Badge>
        <Badge className="bg-live text-live-foreground">LIVE</Badge>
        <Badge className="bg-paper text-paper-foreground">PAPER</Badge>
        <Badge className="bg-demo text-demo-foreground">DEMO DATA</Badge>
        <Badge className="bg-profit/10 text-profit"><TrendingUp /> +2.40 %</Badge>
        <Badge className="bg-loss/10 text-loss"><TrendingDown /> -1.10 %</Badge>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="outline" size="icon" aria-label="What is Sharpe?"><Info /></Button>
          </TooltipTrigger>
          <TooltipContent>Return per unit of risk (yearly). Above 1 is good.</TooltipContent>
        </Tooltip>
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant="outline">Export</Button></DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuLabel>Download</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem>Excel report</DropdownMenuItem>
            <DropdownMenuItem>Trades CSV</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => toast.success("Backtest finished", { description: "248 trades" })}>
          Success toast
        </Button>
        <Button variant="outline" onClick={() => toast.error("Dhan key not set", { description: "Showing DEMO data" })}>
          Error toast
        </Button>
        <Button variant="outline" onClick={() => toast.warning("Holiday list ends 25-Dec-2026")}>Warning toast</Button>
        <Button variant="outline" onClick={() => toast.info("Market opens at 09:15 IST")}>Info toast</Button>
      </div>
    </div>
  );
}

// Every kind of form input.
function Forms() {
  const [lots, setLots] = useState([2]);
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2">
        <Label htmlFor="kit-capital">Capital (Rs)</Label>
        <Input id="kit-capital" type="number" defaultValue={500000} />
      </div>
      <div className="space-y-2">
        <Label>Underlying</Label>
        <Select defaultValue="NIFTY">
          <SelectTrigger className="w-full"><SelectValue placeholder="Pick one" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="NIFTY">NIFTY</SelectItem>
            <SelectItem value="BANKNIFTY">BANKNIFTY</SelectItem>
            <SelectItem value="FINNIFTY">FINNIFTY</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2 md:col-span-2">
        <Label htmlFor="kit-note">Trade note</Label>
        <Textarea id="kit-note" placeholder="Why did you take this trade?" />
      </div>
      <div className="flex items-center gap-2">
        <Switch id="kit-switch" defaultChecked />
        <Label htmlFor="kit-switch">Include taxes</Label>
      </div>
      <div className="flex items-center gap-2">
        <Checkbox id="kit-check" defaultChecked />
        <Label htmlFor="kit-check">Skip expiry days</Label>
      </div>
      <RadioGroup defaultValue="pct" className="flex gap-4">
        <div className="flex items-center gap-2"><RadioGroupItem value="pct" id="kit-pct" /><Label htmlFor="kit-pct">% of price</Label></div>
        <div className="flex items-center gap-2"><RadioGroupItem value="pts" id="kit-pts" /><Label htmlFor="kit-pts">Rs points</Label></div>
      </RadioGroup>
      <ToggleGroup type="single" defaultValue="1y" variant="outline">
        {["1mo", "6mo", "1y", "5y"].map((p) => <ToggleGroupItem key={p} value={p}>{p}</ToggleGroupItem>)}
      </ToggleGroup>
      <div className="space-y-2 md:col-span-2">
        <Label>Lots: <span className="num">{lots[0]}</span></Label>
        <Slider value={lots} onValueChange={setLots} min={1} max={10} step={1} />
      </div>
    </div>
  );
}

// A trade table with numbers lined up and coloured by profit or loss.
function TradeTable() {
  return (
    <Table>
      <TableCaption>Made-up trades. Historical analysis for learning, not a live trading recommendation.</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Date</TableHead><TableHead>Symbol</TableHead><TableHead>Side</TableHead>
          <TableHead className="text-right">Qty</TableHead><TableHead className="text-right">Net P&amp;L</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {TRADES.map((t) => (
          <TableRow key={t.symbol}>
            <TableCell className="num">{formatDate(t.date)}</TableCell>
            <TableCell>{t.symbol}</TableCell>
            <TableCell>{t.side}</TableCell>
            <TableCell className="num text-right">{formatInt(t.qty)}</TableCell>
            <TableCell className={`num text-right ${pnlClass(t.pnl)}`}>{formatRs(t.pnl, 2, true)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// Dialog, confirm dialog, sheet and popover.
function Overlays() {
  return (
    <div className="flex flex-wrap gap-2">
      <Dialog>
        <DialogTrigger asChild><Button variant="outline">Open dialog</Button></DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save strategy</DialogTitle>
            <DialogDescription>Give the strategy a name to find it in the library.</DialogDescription>
          </DialogHeader>
          <Input placeholder="Short straddle 09:20" />
          <DialogFooter>
            <DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose>
            <DialogClose asChild><Button>Save</Button></DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AlertDialog>
        <AlertDialogTrigger asChild><Button variant="destructive"><Power /> Kill switch</Button></AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Stop every strategy?</AlertDialogTitle>
            <AlertDialogDescription>
              Every running strategy stops at once. Open positions stay open unless square-off is on.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep running</AlertDialogCancel>
            <AlertDialogAction className="bg-loss text-loss-foreground hover:bg-loss/90">Stop everything</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <Sheet>
        <SheetTrigger asChild><Button variant="outline">Open sheet</Button></SheetTrigger>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Leg settings</SheetTitle>
            <SheetDescription>Stop-loss, target and trailing for one leg.</SheetDescription>
          </SheetHeader>
          <div className="space-y-2 px-4">
            <Label htmlFor="kit-sl">Stop-loss (%)</Label>
            <Input id="kit-sl" type="number" defaultValue={25} />
          </div>
          <SheetFooter><Button>Apply</Button></SheetFooter>
        </SheetContent>
      </Sheet>
      <Popover>
        <PopoverTrigger asChild><Button variant="outline">Open popover</Button></PopoverTrigger>
        <PopoverContent className="text-sm">
          Max pain is the strike where option buyers lose the most at expiry.
        </PopoverContent>
      </Popover>
    </div>
  );
}

// Loading, progress, alerts and long lists.
function Feedback() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">Loading (skeleton)</p>
        <Skeleton className="h-6 w-2/3" /><Skeleton className="h-6 w-1/2" /><Skeleton className="h-24 w-full" />
      </div>
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">Background job: 62 %</p>
        <Progress value={62} />
        <Alert>
          <Info />
          <AlertTitle>Market closed</AlertTitle>
          <AlertDescription>Next open: Monday 09:15 IST.</AlertDescription>
        </Alert>
        <Alert variant="destructive">
          <Power />
          <AlertTitle>Daily loss limit reached</AlertTitle>
          <AlertDescription>All strategies were stopped.</AlertDescription>
        </Alert>
        <Alert className="border-demo/40 bg-demo/10 text-demo">
          <AlertTitle>DEMO DATA</AlertTitle>
          <AlertDescription className="text-demo">No Dhan keys: these prices are made up.</AlertDescription>
        </Alert>
      </div>
      <ScrollArea className="h-40 rounded-lg border p-3 md:col-span-2">
        {Array.from({ length: 30 }, (_, i) => (
          <p key={i} className="num text-sm">{formatTimeIST(Date.UTC(2026, 10, 16, 3, 45 + i))} · NIFTY tick {i + 1}</p>
        ))}
      </ScrollArea>
    </div>
  );
}

// Tabs, accordion, collapsible, command search and calendar.
function Navigation() {
  const [day, setDay] = useState(() => new Date(2026, 10, 16));
  const [open, setOpen] = useState(false);
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <div className="space-y-4">
        <Tabs defaultValue="day">
          <TabsList>
            <TabsTrigger value="day">Day-wise</TabsTrigger>
            <TabsTrigger value="month">Month-wise</TabsTrigger>
            <TabsTrigger value="weekday">By weekday</TabsTrigger>
          </TabsList>
          <TabsContent value="day" className="text-sm text-muted-foreground">One square per trading day.</TabsContent>
          <TabsContent value="month" className="text-sm text-muted-foreground">Years down, months across.</TabsContent>
          <TabsContent value="weekday" className="text-sm text-muted-foreground">Total P&amp;L by weekday.</TabsContent>
        </Tabs>
        <Accordion type="single" collapsible>
          <AccordionItem value="what">
            <AccordionTrigger>What does this page do?</AccordionTrigger>
            <AccordionContent>Backtests a multi-leg options strategy on stored 1-minute data.</AccordionContent>
          </AccordionItem>
          <AccordionItem value="read">
            <AccordionTrigger>How do I read it?</AccordionTrigger>
            <AccordionContent>All P&amp;L is after brokerage, taxes and slippage.</AccordionContent>
          </AccordionItem>
        </Accordion>
        <Collapsible open={open} onOpenChange={setOpen} className="space-y-2">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm"><ChevronsUpDown /> {open ? "Hide" : "Show"} trading costs</Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="rounded-lg border p-3 text-sm">
            Brokerage Rs 20 per order, STT, exchange, SEBI, stamp duty and GST.
          </CollapsibleContent>
        </Collapsible>
        <Command className="rounded-lg border">
          <CommandInput placeholder="Search a symbol…" />
          <CommandList>
            <CommandEmpty>No symbol found.</CommandEmpty>
            <CommandGroup heading="Indices">
              {SYMBOLS.slice(0, 2).map((s) => <CommandItem key={s}><Search /> {s}</CommandItem>)}
            </CommandGroup>
            <CommandSeparator />
            <CommandGroup heading="Stocks and ETFs">
              {SYMBOLS.slice(2).map((s) => <CommandItem key={s}>{s}</CommandItem>)}
            </CommandGroup>
          </CommandList>
        </Command>
      </div>
      <div className="space-y-2">
        <Calendar mode="single" selected={day} onSelect={setDay} defaultMonth={new Date(2026, 10, 1)}
          className="rounded-lg border" />
        <p className="text-sm text-muted-foreground">Picked: <span className="num">{day ? formatDate(
          `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`)
          : "none"}</span></p>
      </div>
    </div>
  );
}

// The whole showcase.
export default function UiKit() {
  return (
    <div className="space-y-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">UI kit</h1>
          <p className="text-muted-foreground">Every design-system component with made-up data (development only).</p>
        </div>
        <ThemeToggle />
      </div>

      <Section title="MarketLens components"><AppComponents /></Section>
      <Separator />
      <Section title="Colours"><Swatches /><TradingColours /></Section>
      <Separator />
      <Section title="Fonts and number formats"><Typography /></Section>
      <Separator />
      <Section title="Cards">
        <div className="grid gap-4 sm:grid-cols-3">
          {[["Total P&L", 48750.5, 9.75], ["Max drawdown", -12400, -2.48], ["Win rate", null, 0]].map(([label, rs, pct]) => (
            <Card key={label}>
              <CardHeader>
                <CardDescription>{label}</CardDescription>
                <CardTitle className={`num text-2xl ${pnlClass(rs)}`}>{rs === null ? "58.20 %" : formatRs(rs, 0, true)}</CardTitle>
              </CardHeader>
              <CardContent className={`num text-sm ${pnlClass(pct)}`}>{formatPct(pct, 2, true)} of capital</CardContent>
              <CardFooter className="text-xs text-muted-foreground">
                Updated {formatRelative(SAMPLE_NOW - 300000, SAMPLE_NOW)}
              </CardFooter>
            </Card>
          ))}
        </div>
      </Section>
      <Separator />
      <Section title="Buttons, badges, tooltip, menu and toasts"><Buttons /></Section>
      <Separator />
      <Section title="Form inputs"><Forms /></Section>
      <Separator />
      <Section title="Table"><TradeTable /></Section>
      <Separator />
      <Section title="Dialogs, sheet and popover"><Overlays /></Section>
      <Separator />
      <Section title="Loading, progress, alerts and scrolling"><Feedback /></Section>
      <Separator />
      <Section title="Tabs, accordion, collapsible, search and calendar"><Navigation /></Section>
    </div>
  );
}
