// Market hours in India: 09:15 to 15:30, Monday to Friday (exchange holidays are not known here).

// The time now in India as { weekday: 0-6 (0 = Sunday), minutes since midnight }.
export function indiaNow(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit",
    minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value;
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday"));
  return { weekday, minutes: Number(get("hour")) * 60 + Number(get("minute")) };
}

// True between 09:15 and 15:30 IST on a weekday.
export function isMarketOpen(date = new Date()) {
  const { weekday, minutes } = indiaNow(date);
  return weekday >= 1 && weekday <= 5 && minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 30;
}
