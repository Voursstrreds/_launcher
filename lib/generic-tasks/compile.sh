mkdir -p ./bin

for i in {1..20};
do
    gcc -o ./bin/generic-task-$i -DPROCESS_NUM=$i -DPROCESS_COUNT=20 ./src/generic-task.c
done

for i in {1..4};
do
    gcc -o ./bin/generic-group-$i -DGROUP_NUM=$i -DPROCESS_COUNT=20 -DGROUP_COUNT=4 ./src/groups.c
done

