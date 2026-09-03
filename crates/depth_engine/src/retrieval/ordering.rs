/// U-shaped Lost-in-the-Middle context permutation.
///
/// Places highest-scoring contexts at the beginning and end of the prompt context,
/// where LLM attention weight is strongest:
/// Input ranks:  [0, 1, 2, 3, 4]
/// Output ranks: [0, 2, 4, 3, 1]

pub fn reorder_lost_in_the_middle<T>(items: Vec<T>) -> Vec<T> {
    if items.len() <= 2 {
        return items;
    }

    let n = items.len();
    let mut reordered: Vec<Option<T>> = (0..n).map(|_| None).collect();
    let mut left = 0;
    let mut right = n - 1;

    for (i, item) in items.into_iter().enumerate() {
        if i % 2 == 0 {
            reordered[left] = Some(item);
            left += 1;
        } else {
            reordered[right] = Some(item);
            if right > 0 {
                right -= 1;
            }
        }
    }

    reordered.into_iter().flatten().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_and_small() {
        let empty: Vec<i32> = vec![];
        assert_eq!(reorder_lost_in_the_middle(empty), Vec::<i32>::new());

        let single = vec![42];
        assert_eq!(reorder_lost_in_the_middle(single), vec![42]);

        let double = vec![1, 2];
        assert_eq!(reorder_lost_in_the_middle(double), vec![1, 2]);
    }

    #[test]
    fn test_odd_contexts() {
        // Input ranks: 0, 1, 2, 3, 4
        let items = vec![0, 1, 2, 3, 4];
        let reordered = reorder_lost_in_the_middle(items);
        assert_eq!(reordered, vec![0, 2, 4, 3, 1]);
    }

    #[test]
    fn test_even_contexts() {
        // Input ranks: 0, 1, 2, 3
        let items = vec![0, 1, 2, 3];
        let reordered = reorder_lost_in_the_middle(items);
        assert_eq!(reordered, vec![0, 2, 3, 1]);
    }
}
