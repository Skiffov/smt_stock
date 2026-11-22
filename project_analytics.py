"""Read-only BOM-vs-material-movement analytics for SMT Stock.

A net issue is not physical consumption: issued - received returns.
The BOM number is treated as the total planned quantity for the project.
"""


def project_material_rows(connection, project_id):
    """One row per BOM component. Pre-aggregate issue rows to avoid join multiplication."""
    rows = connection.execute('''
        WITH movement AS (
          SELECT req.project_id, ri.component_id,
                 SUM(a.issued_qty) AS issued,
                 SUM(a.returned_qty) AS returned,
                 COUNT(a.id) AS issue_operations
          FROM allocations a
          JOIN request_items ri ON ri.id = a.request_item_id
          JOIN requests req ON req.id = ri.request_id
          WHERE req.project_id = ?
          GROUP BY req.project_id, ri.component_id
        )
        SELECT pc.component_id, c.code, c.name, c.unit,
               pc.required_qty AS planned,
               COALESCE(m.issued, 0) AS issued,
               COALESCE(m.returned, 0) AS returned,
               COALESCE(m.issued, 0) - COALESCE(m.returned, 0) AS net,
               COALESCE(m.issue_operations, 0) AS operations
        FROM project_components pc
        JOIN components c ON c.id = pc.component_id
        LEFT JOIN movement m ON m.project_id = pc.project_id AND m.component_id = pc.component_id
        WHERE pc.project_id = ?
        ORDER BY c.code COLLATE NOCASE
    ''', (project_id, project_id)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['difference'] = item['net'] - item['planned']
        item['percent'] = round(100 * item['net'] / item['planned'], 1)
        item['chart_max'] = max(item['planned'], item['net'], 1)
        item['plan_width'] = round(100 * item['planned'] / item['chart_max'], 1)
        item['net_width'] = round(100 * item['net'] / item['chart_max'], 1)
        item['difference_state'] = 'over' if item['difference'] > 0 else ('under' if item['difference'] < 0 else 'match')
        result.append(item)
    return result


def project_material_summary(rows):
    """Never sum physical quantities across different component units."""
    return {
        'components': len(rows),
        'above': sum(row['difference'] > 0 for row in rows),
        'below': sum(row['difference'] < 0 for row in rows),
        'exact': sum(row['difference'] == 0 for row in rows),
        'with_issues': sum(row['operations'] > 0 for row in rows),
        'no_issues': sum(row['operations'] == 0 for row in rows),
    }
