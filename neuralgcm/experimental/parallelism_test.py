# Copyright 2025 Google LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests distributed parallelism helper module Mesh and its methods."""

import collections
import re

from absl.testing import absltest
from absl.testing import parameterized
import chex
import jax
from jax import config  # pylint: disable=g-importing-member
from neuralgcm.experimental import coordax as cx
from neuralgcm.experimental import parallelism
import numpy as np


class MeshTest(parameterized.TestCase):
  """Tests Mesh class and its sharding constraint methods."""

  def test_constructor(self):
    with self.subTest('no_sharding'):
      no_sharding_mesh = parallelism.Mesh(spmd_mesh=None)
      self.assertIsNone(no_sharding_mesh.spmd_mesh)
      self.assertEqual(no_sharding_mesh.axis_names, ())
      self.assertEqual(no_sharding_mesh.shape, collections.OrderedDict())

    with self.subTest('222_mesh'):
      spmd_mesh = jax.sharding.Mesh(
          np.array(jax.devices()).reshape((2, 2, 2)), axis_names=['z', 'x', 'y']
      )
      mesh = parallelism.Mesh(spmd_mesh=spmd_mesh)
      self.assertEqual(mesh.spmd_mesh, spmd_mesh)
      self.assertEqual(mesh.axis_names, ('z', 'x', 'y'))
      self.assertEqual(
          mesh.shape, collections.OrderedDict([('z', 2), ('x', 2), ('y', 2)])
      )

  def test_partitions_checked_on_init(self):
    spmd_mesh = jax.sharding.Mesh(
        np.array(jax.devices()).reshape((2, 2, 2)), axis_names=['z', 'x', 'y']
    )

    with self.subTest('valid_array_partitions'):
      array_partitions = {
          'vertical': (('z', 'x', 'y'), None, None),
          'horizontal': (None, ('z', 'x'), 'y'),
      }
      parallelism.Mesh(spmd_mesh, array_partitions=array_partitions)

    with self.subTest('invalid_array_partitions_unknown_name'):
      array_partitions = {'unknownB': (('B', 'x', 'y'), None, 'z')}
      with self.assertRaisesRegex(ValueError, re.escape(r'use axes not in')):
        parallelism.Mesh(spmd_mesh, array_partitions=array_partitions)

    with self.subTest('invalid_array_partitions_duplicate_axis'):
      array_partitions = {'p': (('x', 'x', 'y'), None, 'z')}
      with self.assertRaisesRegex(
          ValueError, re.escape(r'Encountered duplicate')
      ):
        parallelism.Mesh(spmd_mesh, array_partitions=array_partitions)

    with self.subTest('valid_field_partitions'):
      field_partitions = {
          'vertical': {'level': ('z', 'x', 'y'), 'layer': 'z'},
          'horizontal': {'lon': ('z', 'x'), 'lat': 'y'},
      }
      parallelism.Mesh(spmd_mesh, field_partitions=field_partitions)

    with self.subTest('invalid_field_partitions_unknown_name'):
      field_partitions = {'unknownB': {'level': ('B', 'x', 'y')}}
      with self.assertRaisesRegex(ValueError, re.escape(r'use axes not in')):
        parallelism.Mesh(spmd_mesh, field_partitions=field_partitions)

    with self.subTest('invalid_field_partitions_duplicate_axis'):
      field_partitions = {'p': {'level': ('z', 'z', 'x')}}
      with self.assertRaisesRegex(
          ValueError, re.escape(r'Encountered duplicate')
      ):
        parallelism.Mesh(spmd_mesh, field_partitions=field_partitions)

  def test_array_constraint(self):
    spmd_mesh = jax.sharding.Mesh(
        np.array(jax.devices()).reshape((2, 2, 2)), axis_names=['z', 'x', 'y']
    )
    array_partitions = {
        'vertical': (('z', 'x', 'y'), None, None),
        'horizontal': (None, ('z', 'x'), 'y'),
        'replicated': (None, None, None),
    }
    mesh = parallelism.Mesh(
        spmd_mesh=spmd_mesh, array_partitions=array_partitions
    )
    shape = (16, 8, 14)
    input_array = np.ones(shape)
    array_vertical = mesh.with_sharding_constraint(input_array, 'vertical')
    actual_shard_shape = array_vertical.sharding.shard_shape(shape)
    self.assertEqual(actual_shard_shape, (16 // (2 * 2 * 2), 8, 14))
    array_horizontal = mesh.with_sharding_constraint(
        input_array, 'horizontal'
    )
    actual_shard_shape = array_horizontal.sharding.shard_shape(shape)
    self.assertEqual(actual_shard_shape, (16, 8 // (2 * 2), 14 // 2))
    array_replicated = mesh.with_sharding_constraint(
        input_array, 'replicated'
    )
    actual_shard_shape = array_replicated.sharding.shard_shape(shape)
    self.assertEqual(actual_shard_shape, shape)

  def test_field_constraint(self):
    spmd_mesh = jax.sharding.Mesh(
        np.array(jax.devices()).reshape((2, 2, 2)), axis_names=['z', 'x', 'y']
    )
    field_partitions = {
        'vertical': {'level': ('z', 'x', 'y'), 'layer': 'z'},
        'horizontal': {'lon': ('z', 'x'), 'lat': 'y'},
    }
    mesh = parallelism.Mesh(
        spmd_mesh=spmd_mesh, field_partitions=field_partitions
    )
    shape = (16, 8, 14)
    input_level_field = cx.wrap(np.ones(shape), 'level', 'lon', 'lat')
    input_layer_field = cx.wrap(np.ones(shape), 'layer', 'lon', 'lat')

    field_vertical = mesh.with_sharding_constraint(
        input_level_field, 'vertical'
    )
    actual_shard_shape = field_vertical.data.sharding.shard_shape(shape)
    self.assertEqual(actual_shard_shape, (16 // (2 * 2 * 2), 8, 14))

    field_vertical = mesh.with_sharding_constraint(
        input_layer_field, 'vertical'
    )
    actual_shard_shape = field_vertical.data.sharding.shard_shape(shape)
    self.assertEqual(actual_shard_shape, (16 // 2, 8, 14))

    field_horizontal = mesh.with_sharding_constraint(
        input_layer_field, 'horizontal'
    )
    actual_shard_shape = field_horizontal.data.sharding.shard_shape(shape)
    self.assertEqual(actual_shard_shape, (16, 8 // (2 * 2), 14 // 2))

  def test_mixed_pytree_constraint(self):
    spmd_mesh = jax.sharding.Mesh(
        np.array(jax.devices()).reshape((2, 2, 2)), axis_names=['z', 'x', 'y']
    )
    array_partitions = {
        'vertical': (('z', 'x', 'y'), None),
    }
    field_partitions = {
        'vertical': {'level': ('z', 'x', 'y'), 'layer': 'z'},
    }
    mesh = parallelism.Mesh(
        spmd_mesh=spmd_mesh,
        array_partitions=array_partitions,
        field_partitions=field_partitions,
    )
    input_pytree = {
        'a': np.ones((16, 7)),
        'b': cx.wrap(np.ones((16, 7)), 'level', 'y'),
        'c': np.zeros((32, 7)),
        'd': cx.wrap(np.zeros((2, 16, 7)), 'b', 'level', 'x'),
        'e': cx.wrap(np.arange(32), 'layer'),
    }
    sharded_pytree = mesh.with_sharding_constraint(input_pytree, 'vertical')
    a_shard_shape = sharded_pytree['a'].sharding.shard_shape((16, 7))
    self.assertEqual(a_shard_shape, (16 // (2 * 2 * 2), 7))
    b_shard_shape = sharded_pytree['b'].data.sharding.shard_shape((16, 7))
    self.assertEqual(b_shard_shape, (16 // (2 * 2 * 2), 7))
    c_shard_shape = sharded_pytree['c'].sharding.shard_shape((32, 7))
    self.assertEqual(c_shard_shape, (32 // (2 * 2 * 2), 7))
    d_shard_shape = sharded_pytree['d'].data.sharding.shard_shape((2, 16, 7))
    self.assertEqual(d_shard_shape, (2, 16 // (2 * 2 * 2), 7))
    e_shard_shape = sharded_pytree['e'].data.sharding.shard_shape((32,))
    self.assertEqual(e_shard_shape, (32 // 2,))

  def test_raises_if_arrays_have_different_ranks(self):
    array_partitions = {'vertical': (('z', 'x', 'y'), None)}
    mesh = parallelism.Mesh(spmd_mesh=None, array_partitions=array_partitions)
    with self.assertRaisesRegex(
        ValueError,
        re.escape('All arrays in the pytree must have the same rank.'),
    ):
      input_pytree = {'rank_2': np.ones((16, 7)), 'rank_3': np.ones((16, 7, 9))}
      mesh.with_sharding_constraint(input_pytree, 'vertical')


if __name__ == '__main__':
  chex.set_n_cpu_devices(8)
  config.update('jax_traceback_filtering', 'off')
  absltest.main()
