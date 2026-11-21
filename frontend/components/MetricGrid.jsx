// Lays MetricCard tiles out in a grid: 2 across on phones, up to `columns` (2, 3, 4 or 6) on wide screens.
//
//   <MetricGrid columns={4}>{tiles}</MetricGrid>
const COLUMNS = {
  2: "grid-cols-2",
  3: "grid-cols-2 md:grid-cols-3",
  4: "grid-cols-2 md:grid-cols-4",
  6: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-6",
};

export default function MetricGrid({ columns = 4, children, className = "" }) {
  return <div className={`grid gap-3 ${COLUMNS[columns] || COLUMNS[4]} ${className}`}>{children}</div>;
}
