"""Вісім ізольованих Unit-тестів для SMT Stock. Запуск: python -m unittest -v test_unit"""
import unittest
from project_workflow import ProjectWorkflow
from project_analytics import project_material_summary

class WorkflowUnitTests(unittest.TestCase):
    def test_start_project(self):
        self.assertEqual(ProjectWorkflow.transition('created', 'start'), 'in_production')

    def test_finish_production(self):
        self.assertEqual(ProjectWorkflow.transition('in_production', 'finish'), 'awaiting_return')

    def test_close_returned_project(self):
        self.assertEqual(ProjectWorkflow.transition('awaiting_return', 'close'), 'finished')

    def test_cannot_restart_finished(self):
        with self.assertRaises(ValueError):
            ProjectWorkflow.transition('finished', 'start')

    def test_cannot_finish_unstarted(self):
        with self.assertRaises(ValueError):
            ProjectWorkflow.transition('created', 'finish')

    def test_unknown_status(self):
        with self.assertRaises(ValueError):
            ProjectWorkflow.transition('missing', 'start')

class AnalyticsUnitTests(unittest.TestCase):
    def test_material_summary_counts(self):
        rows = [
            {'difference': 10, 'operations': 2},
            {'difference': -5, 'operations': 1},
            {'difference': 0, 'operations': 0},
        ]
        self.assertEqual(project_material_summary(rows), {
            'components': 3, 'above': 1, 'below': 1,
            'exact': 1, 'with_issues': 2, 'no_issues': 1,
        })

    def test_material_summary_empty(self):
        self.assertEqual(project_material_summary([]), {
            'components': 0, 'above': 0, 'below': 0,
            'exact': 0, 'with_issues': 0, 'no_issues': 0,
        })

if __name__ == '__main__':
    unittest.main(verbosity=2)
